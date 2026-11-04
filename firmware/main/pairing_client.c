#include "pairing_client.h"
#include "passport_tv.h"
#include "lwip/dns.h"
#include "lwip/tcpip.h"
#include "lwip/sockets.h"
#include "lwip/inet.h"
#include "http_parser.h"
#include <stdatomic.h>
#include <errno.h>
#include <fcntl.h>
#include "cJSON.h"
#include "esp_tls.h"
#include "esp_crt_bundle.h"
#include "esp_mac.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "esp_netif_sntp.h"
#include "esp_sntp.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs.h"
#include <time.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#ifndef PTV_DEFAULT_ORIGIN
#define PTV_DEFAULT_ORIGIN ""
#endif

// One atomic NVS value associates each credential with its issuer. Legacy
// unscoped credentials are imported only by the private managed migration.
typedef struct { char origin[TV_ORIGIN_CAP],secret[65],code[5]; } binding_t;
const char *pairing_client_default_origin(void) { return PTV_DEFAULT_ORIGIN; }
static bool origin_valid(const char *origin) {
    char normalized[TV_ORIGIN_CAP];
    return origin && memchr(origin,0,TV_ORIGIN_CAP) &&
        tv_origin_from_input(origin,80,NULL,normalized) && !strcmp(origin,normalized);
}
static bool secret_valid(const char secret[65]) {
    return secret[64]==0 && strspn(secret,"0123456789abcdef")==64;
}
static bool code_valid(const char code[5]) {
    return code[4]==0 && (!code[0] || (strlen(code)==4 && strspn(code,"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")==4));
}
static esp_err_t read_binding(const char *key,binding_t *b) {
    memset(b,0,sizeof *b);nvs_handle_t h;size_t n=sizeof *b;
    esp_err_t e=nvs_open("ptv_auth",NVS_READONLY,&h);
    if(e==ESP_ERR_NVS_NOT_FOUND)return ESP_ERR_NOT_FOUND;
    if(e!=ESP_OK)return e;
    e=nvs_get_blob(h,key,b,&n);nvs_close(h);
    if(e==ESP_ERR_NVS_NOT_FOUND)return ESP_ERR_NOT_FOUND;
    if(e==ESP_OK && (n!=sizeof *b || !origin_valid(b->origin) || !secret_valid(b->secret) || !code_valid(b->code)))e=ESP_ERR_INVALID_STATE;
    return e;
}
static esp_err_t write_binding(const char *key,const binding_t *b) {
    nvs_handle_t h;esp_err_t e=nvs_open("ptv_auth",NVS_READWRITE,&h);
    if(e!=ESP_OK)return e;
    e=nvs_set_blob(h,key,b,sizeof *b);if(e==ESP_OK)e=nvs_commit(h);nvs_close(h);return e;
}
esp_err_t pairing_client_migrate(void) {
    char origin[TV_ORIGIN_CAP];
#ifndef PTV_LEGACY_ORIGIN
    return ESP_ERR_INVALID_STATE;
#else
    char legacy[TV_ORIGIN_CAP];
    if(!tv_origin_from_input("",80,PTV_DEFAULT_ORIGIN,origin) ||
       !tv_origin_from_input("",80,PTV_LEGACY_ORIGIN,legacy) || strcmp(origin,legacy))return ESP_ERR_INVALID_STATE;
#endif
    const char *old_keys[]={"active","pending"},*new_keys[]={"active_v1","pending_v1"};
    for(unsigned i=0;i<2;i++){
        binding_t b;esp_err_t e=read_binding(new_keys[i],&b);
        memset(&b,0,sizeof b);
        if(e==ESP_OK)continue;
        if(e!=ESP_ERR_NOT_FOUND)return e;
        nvs_handle_t h;e=nvs_open("ptv_auth",NVS_READONLY,&h);
        if(e!=ESP_OK)return e;
        size_t n=sizeof b.secret;e=nvs_get_str(h,old_keys[i],b.secret,&n);nvs_close(h);
        if(e==ESP_ERR_NVS_NOT_FOUND)continue;
        if(e!=ESP_OK || n!=65 || !secret_valid(b.secret)){memset(&b,0,sizeof b);return ESP_ERR_INVALID_STATE;}
        strcpy(b.origin,origin);e=write_binding(new_keys[i],&b);memset(&b,0,sizeof b);
        if(e!=ESP_OK)return e;
    }
    return ESP_OK;
}
static int remaining_ms(int64_t deadline) {
    int64_t left=(deadline-esp_timer_get_time())/1000;
    return left>0?(int)left:0;
}
static bool cancelled(bool cancellable) { return cancellable && passport_tv_connection_cancelled(); }
// DNS completion may outlive a timed-out caller: the TCP/IP callback owns its
// own reference, and never retains the caller's stack or connection output.
struct lookup { atomic_uint refs;atomic_bool done;char host[TV_ORIGIN_CAP];ip_addr_t address;bool found; };
static void lookup_release(struct lookup *q) { if(atomic_fetch_sub(&q->refs,1)==1)free(q); }
static void lookup_done(const char *name,const ip_addr_t *address,void *arg) {
    (void)name;struct lookup *q=arg;
    q->found=address!=NULL;if(address)q->address=*address;
    atomic_store(&q->done,true);lookup_release(q);
}
static void lookup_start(void *arg) {
    struct lookup *q=arg;ip_addr_t address;
    err_t e=dns_gethostbyname_addrtype(q->host,&address,lookup_done,q,LWIP_DNS_ADDRTYPE_IPV4);
    if(e!=ERR_INPROGRESS)lookup_done(q->host,e==ERR_OK?&address:NULL,q);
}
static esp_err_t resolve_host(const char *host,char address[16],int64_t deadline,bool cancellable) {
    ip4_addr_t numeric;
    if(ip4addr_aton(host,&numeric)){ip4addr_ntoa_r(&numeric,address,16);return ESP_OK;}
    struct lookup *q=calloc(1,sizeof *q);if(!q)return ESP_ERR_NO_MEM;
    atomic_init(&q->refs,2);atomic_init(&q->done,false);strcpy(q->host,host);
    if(tcpip_try_callback(lookup_start,q)!=ERR_OK){lookup_release(q);lookup_release(q);return ESP_FAIL;}
    while(!atomic_load(&q->done) && remaining_ms(deadline)>0 && !cancelled(cancellable))vTaskDelay(pdMS_TO_TICKS(20));
    esp_err_t e=ESP_ERR_TIMEOUT;
    if(atomic_load(&q->done) && q->found && !cancelled(cancellable) && remaining_ms(deadline)>0){
        ip4addr_ntoa_r(ip_2_ip4(&q->address),address,16);e=ESP_OK;
    }
    lookup_release(q);return e;
}
struct response { char data[512];size_t used;bool overflow,complete,cancellable;int64_t deadline; };
static int plain_body(http_parser *parser,const char *data,size_t n) {
    struct response *r=parser->data;
    if(n>=sizeof r->data-r->used){r->overflow=true;return 1;}
    memcpy(r->data+r->used,data,n);r->used+=n;r->data[r->used]=0;return 0;
}
static int plain_complete(http_parser *parser) { ((struct response*)parser->data)->complete=true;return 0; }
static bool socket_ready(int fd,bool write,int64_t deadline,bool cancellable) {
    while(remaining_ms(deadline)>0 && !cancelled(cancellable)){
        fd_set set;FD_ZERO(&set);FD_SET(fd,&set);
        int left=remaining_ms(deadline);if(left>100)left=100;
        struct timeval wait={.tv_sec=0,.tv_usec=left*1000};
        int n=select(fd+1,write?NULL:&set,write?&set:NULL,NULL,&wait);
        if(n>0)return true;
        if(n<0 && errno!=EINTR)return false;
    }
    return false;
}
// Both HTTP and HTTPS use nonblocking transport and the same bounded parser.
static esp_err_t exchange(const char *address,uint16_t port,const char *authority,const char *server_name,
                           const char *path,const char *authorization,const char *body,
                           struct response *reply,int *status,bool https) {
    int fd=-1;esp_tls_t *tls=NULL;esp_err_t e=ESP_FAIL;char *wire=NULL;
    if(https){
        tls=esp_tls_init();if(!tls)return ESP_ERR_NO_MEM;
        esp_tls_cfg_t cfg={.non_block=true,.timeout_ms=100,
            .common_name=server_name,.crt_bundle_attach=esp_crt_bundle_attach};
        for(;;){
            if(!remaining_ms(reply->deadline) || cancelled(reply->cancellable)){e=ESP_ERR_TIMEOUT;goto done;}
            int left=remaining_ms(reply->deadline);
            if(!left){e=ESP_ERR_TIMEOUT;goto done;}
            cfg.timeout_ms=left>100?100:left;
            esp_tls_conn_state_t state;
            if(esp_tls_get_conn_state(tls,&state)!=ESP_OK)goto done;
            cfg.non_block=true;
            if(state==ESP_TLS_CONNECTING){
                if(esp_tls_get_conn_sockfd(tls,&fd)!=ESP_OK)goto done;
                // IDF 5.5.3's connecting select consumes its fd_sets on timeout.
                // Poll our own sets, then skip that select; the socket itself
                // stays nonblocking throughout the handshake and data exchange.
                if(!socket_ready(fd,true,reply->deadline,reply->cancellable)){e=ESP_ERR_TIMEOUT;goto done;}
                int error=0;socklen_t n=sizeof error;
                if(getsockopt(fd,SOL_SOCKET,SO_ERROR,&error,&n)<0 || error)goto done;
                cfg.non_block=false;
            }
            int connected=esp_tls_conn_new_async(address,(int)strlen(address),port,&cfg,tls);
            if(connected<0)goto done;
            if(connected==1)break;
            vTaskDelay(pdMS_TO_TICKS(10));
        }
        if(esp_tls_get_conn_sockfd(tls,&fd)!=ESP_OK)goto done;
    }else {
        fd=socket(AF_INET,SOCK_STREAM,IPPROTO_TCP);if(fd<0)goto done;
        if(fcntl(fd,F_SETFL,O_NONBLOCK)<0)goto done;
        struct sockaddr_in target={.sin_family=AF_INET,.sin_port=htons(port)};target.sin_addr.s_addr=inet_addr(address);
        int connected=connect(fd,(struct sockaddr*)&target,sizeof target);
        if(connected<0 && errno!=EINPROGRESS)goto done;
        if(connected<0){
            if(!socket_ready(fd,true,reply->deadline,reply->cancellable)){e=ESP_ERR_TIMEOUT;goto done;}
            int error=0;socklen_t n=sizeof error;
            if(getsockopt(fd,SOL_SOCKET,SO_ERROR,&error,&n)<0 || error)goto done;
        }
    }
    size_t cap=strlen(body)+strlen(authority)+strlen(authorization)+strlen(path)+160;
    wire=malloc(cap);if(!wire){e=ESP_ERR_NO_MEM;goto done;}
    int n=snprintf(wire,cap,"POST %s HTTP/1.1\r\nHost: %s\r\nAuthorization: %s\r\nContent-Type: application/json\r\nContent-Length: %u\r\nConnection: close\r\n\r\n%s",
        path,authority,authorization,(unsigned)strlen(body),body);
    if(n<0 || (size_t)n>=cap)goto done;
    for(size_t sent=0;sent<(size_t)n;){
        if(!remaining_ms(reply->deadline) || cancelled(reply->cancellable)){e=ESP_ERR_TIMEOUT;goto done;}
        int count=tls?esp_tls_conn_write(tls,wire+sent,(size_t)n-sent):send(fd,wire+sent,(size_t)n-sent,0);
        if(tls && (count==ESP_TLS_ERR_SSL_WANT_READ || count==ESP_TLS_ERR_SSL_WANT_WRITE)){
            if(!socket_ready(fd,count==ESP_TLS_ERR_SSL_WANT_WRITE,reply->deadline,reply->cancellable)){e=ESP_ERR_TIMEOUT;goto done;}
            continue;
        }
        if(!tls && count<0 && (errno==EAGAIN || errno==EWOULDBLOCK || errno==EINTR)){
            if(!socket_ready(fd,true,reply->deadline,reply->cancellable)){e=ESP_ERR_TIMEOUT;goto done;}
            continue;
        }
        if(count<=0)goto done;
        sent+=(size_t)count;
    }
    http_parser parser;http_parser_init(&parser,HTTP_RESPONSE);parser.data=reply;
    http_parser_settings settings={.on_body=plain_body,.on_message_complete=plain_complete};
    size_t received=0;
    while(!reply->complete){
        if(!remaining_ms(reply->deadline) || cancelled(reply->cancellable)){e=ESP_ERR_TIMEOUT;goto done;}
        char data[512];int count=tls?esp_tls_conn_read(tls,data,sizeof data):recv(fd,data,sizeof data,0);
        if(tls && (count==ESP_TLS_ERR_SSL_WANT_READ || count==ESP_TLS_ERR_SSL_WANT_WRITE)){
            if(!socket_ready(fd,count==ESP_TLS_ERR_SSL_WANT_WRITE,reply->deadline,reply->cancellable)){e=ESP_ERR_TIMEOUT;goto done;}
            continue;
        }
        if(!tls && count<0 && (errno==EAGAIN || errno==EWOULDBLOCK || errno==EINTR)){
            if(!socket_ready(fd,false,reply->deadline,reply->cancellable)){e=ESP_ERR_TIMEOUT;goto done;}
            continue;
        }
        if(count<0)goto done;
        received+=(size_t)count;if(received>4096){e=ESP_ERR_INVALID_RESPONSE;goto done;}
        size_t parsed=http_parser_execute(&parser,&settings,data,(size_t)count);
        if(parsed!=(size_t)count || HTTP_PARSER_ERRNO(&parser)!=HPE_OK || reply->overflow){e=ESP_ERR_INVALID_RESPONSE;goto done;}
        if(!count && !reply->complete)goto done;
    }
    *status=parser.status_code;e=ESP_OK;
 done:
    if(wire){memset(wire,0,strlen(wire));free(wire);}
    if(tls)esp_tls_conn_destroy(tls);else if(fd>=0)close(fd);
    return e;
}
static esp_err_t request(const char *origin,const char *path,const char *secret,const char *code,
                         tv_connection_t *out,int64_t deadline,bool cancellable) {
    bool https=!strncmp(origin,"https://",8);
    if(cancelled(cancellable))return ESP_ERR_INVALID_STATE;
    if(https && time(NULL)<1700000000){
        if(!esp_sntp_enabled()){
            esp_sntp_config_t ntp=ESP_NETIF_SNTP_DEFAULT_CONFIG("pool.ntp.org");
            if(esp_netif_sntp_init(&ntp)!=ESP_OK)return ESP_FAIL;
        }
        int64_t ntp_deadline=esp_timer_get_time()+10000000;if(ntp_deadline>deadline)ntp_deadline=deadline;
        while(time(NULL)<1700000000){
            int left=remaining_ms(ntp_deadline);
            if(left<=0 || cancelled(cancellable))return ESP_ERR_TIMEOUT;
            if(left>100)left=100;
            esp_err_t sync=esp_netif_sntp_sync_wait(pdMS_TO_TICKS(left));
            if(sync==ESP_OK)break;
            if(sync!=ESP_ERR_TIMEOUT)return sync;
        }
    }
    int left=remaining_ms(deadline);if(left<=0 || cancelled(cancellable))return ESP_ERR_TIMEOUT;
    if(left>12000)left=12000;
    int64_t http_deadline=esp_timer_get_time()+(int64_t)left*1000;
    const char *connect_origin=origin,*server_name=NULL;
#ifdef PTV_CONNECT_ORIGIN
    // Build-defined gateway/SNI policy applies only to its matching issuer.
    if(!strcmp(origin,PTV_DEFAULT_ORIGIN)){connect_origin=PTV_CONNECT_ORIGIN;server_name=PTV_SERVER_NAME;}
#endif
    const char *authority=connect_origin+(https?8:7),*colon=strchr(authority,':');
    char name[TV_ORIGIN_CAP],address[16];size_t host_len=colon?(size_t)(colon-authority):strlen(authority);
    memcpy(name,authority,host_len);name[host_len]=0;
    uint16_t server_port=colon?(uint16_t)atoi(colon+1):(https?443:80);
    esp_err_t resolved=resolve_host(name,address,http_deadline,cancellable);if(resolved!=ESP_OK)return resolved;
    // Connect to the resolved address, preserving the original TLS identity/Host.
    if(!server_name)server_name=name;
    uint8_t mac[6];if(esp_efuse_mac_get_default(mac)!=ESP_OK)return ESP_FAIL;
    char device[13],authorization[72],url[TV_ORIGIN_CAP+32];
    snprintf(device,sizeof device,"%02x%02x%02x%02x%02x%02x",mac[0],mac[1],mac[2],mac[3],mac[4],mac[5]);
    snprintf(authorization,sizeof authorization,"Bearer %s",secret);
    snprintf(url,sizeof url,"%s://%s:%u%s",https?"https":"http",address,(unsigned)server_port,path);
    cJSON *j=cJSON_CreateObject();if(!j)return ESP_ERR_NO_MEM;
    bool built=cJSON_AddStringToObject(j,"device",device)!=NULL;
    if(code)built=built && cJSON_AddStringToObject(j,"code",code)!=NULL;
    char *body=built?cJSON_PrintUnformatted(j):NULL;cJSON_Delete(j);
    if(!body){memset(authorization,0,sizeof authorization);return ESP_ERR_NO_MEM;}
    struct response reply={.deadline=http_deadline,.cancellable=cancellable};
    left=remaining_ms(http_deadline);if(!left){memset(body,0,strlen(body));cJSON_free(body);return ESP_ERR_TIMEOUT;}
    esp_err_t e=ESP_FAIL;int status=0;
    e=exchange(address,server_port,!strcmp(connect_origin,origin)?authority:server_name,server_name,
        path,authorization,body,&reply,&status,https);
    memset(body,0,strlen(body));cJSON_free(body);memset(authorization,0,sizeof authorization);
    if(e==ESP_OK && (!remaining_ms(reply.deadline) || cancelled(cancellable)))e=ESP_ERR_TIMEOUT;
    if(e==ESP_OK && status==403)e=ESP_ERR_NOT_FOUND;
    else if(e==ESP_OK && (status!=200 || reply.overflow))e=ESP_ERR_INVALID_RESPONSE;
    if(e==ESP_OK){
        j=memchr(reply.data,0,reply.used) || strstr(reply.data,"\\u0000")?NULL:
            cJSON_ParseWithLengthOpts(reply.data,reply.used+1,NULL,true);
        const cJSON *host=cJSON_GetObjectItemCaseSensitive(j,"host"),
            *port=cJSON_GetObjectItemCaseSensitive(j,"port"),*ticket=cJSON_GetObjectItemCaseSensitive(j,"ticket");
        tv_connection_t next=*out;
        if(!cJSON_IsString(host)||strlen(host->valuestring)>=sizeof next.host || !cJSON_IsString(ticket)||
            strlen(ticket->valuestring)>=sizeof next.token || !cJSON_IsNumber(port)||port->valuedouble!=port->valueint ||
            port->valueint<1 || port->valueint>65535)e=ESP_ERR_INVALID_RESPONSE;
        else {strcpy(next.host,host->valuestring);strcpy(next.token,ticket->valuestring);next.port=(uint16_t)port->valueint;
            if(tv_connection_valid(&next))*out=next;else e=ESP_ERR_INVALID_RESPONSE;}
        memset(&next,0,sizeof next);cJSON_Delete(j);
    }
    memset(&reply,0,sizeof reply);return e;
}
static esp_err_t recover(const char *origin,const char *code,tv_connection_t *out,int64_t deadline,bool cancellable,bool *pending_used) {
    *pending_used=false;
    const char *keys[]={"pending_v1","active_v1"};
    for(unsigned i=0;i<(code && code[0]?1u:2u);i++){
        binding_t b;esp_err_t e=read_binding(keys[i],&b);
        if(e==ESP_OK && !strcmp(origin,b.origin) && (!code || !code[0] || !strcmp(code,b.code))){
            e=request(origin,"/social/tv/config",b.secret,NULL,out,deadline,cancellable);
            memset(&b,0,sizeof b);
            if(e==ESP_OK)*pending_used=i==0;
            if(e!=ESP_ERR_NOT_FOUND)return e;
        }else {memset(&b,0,sizeof b);if(e!=ESP_OK && e!=ESP_ERR_NOT_FOUND)return e;}
    }
    return ESP_ERR_NOT_FOUND;
}
esp_err_t pairing_client_pair(const char *origin,const char *code,tv_connection_t *out,bool *pending_used) {
    if(!pending_used)return ESP_ERR_INVALID_ARG;
    *pending_used=false;
    if(!origin_valid(origin) || !out || !code)return ESP_ERR_INVALID_ARG;
    char normalized[5]={0};size_t n=strlen(code);
    if(n && n!=4)return ESP_ERR_INVALID_ARG;
    for(unsigned i=0;i<n;i++){
        char c=code[i];if(c>='a'&&c<='z')c-='a'-'A';
        if(!((c>='A'&&c<='Z')||(c>='0'&&c<='9')))return ESP_ERR_INVALID_ARG;
        normalized[i]=c;
    }
    int64_t deadline=esp_timer_get_time()+22000000;
    tv_connection_t next=*out;esp_err_t e=recover(origin,normalized,&next,deadline,false,pending_used);
    if(e==ESP_OK){*out=next;memset(&next,0,sizeof next);return ESP_OK;}
    if(e!=ESP_ERR_NOT_FOUND || !n){memset(&next,0,sizeof next);return e;}
    binding_t b;e=read_binding("pending_v1",&b);
    if(e==ESP_ERR_NOT_FOUND || (e==ESP_OK && (strcmp(b.origin,origin) || strcmp(b.code,normalized)))){
        memset(&b,0,sizeof b);strcpy(b.origin,origin);strcpy(b.code,normalized);
        uint8_t random[32];esp_fill_random(random,sizeof random);
        for(unsigned i=0;i<32;i++)snprintf(b.secret+2*i,3,"%02x",random[i]);
        memset(random,0,sizeof random);e=write_binding("pending_v1",&b);
    }
    // Persist pending before any redemption. Never replace it just for a retry.
    if(e==ESP_OK){
        e=request(origin,"/social/tv/activate",b.secret,normalized,&next,deadline,false);
        if(e!=ESP_OK && e!=ESP_ERR_INVALID_RESPONSE && e!=ESP_ERR_NOT_FOUND && remaining_ms(deadline)>0)
            e=request(origin,"/social/tv/config",b.secret,NULL,&next,deadline,false);
    }
    if(e==ESP_OK){*out=next;*pending_used=true;}
    memset(&next,0,sizeof next);memset(&b,0,sizeof b);memset(normalized,0,sizeof normalized);return e;
}
esp_err_t pairing_client_refresh(const char *origin,tv_connection_t *out,uint32_t timeout_ms) {
    if(!origin_valid(origin) || !out || !timeout_ms)return ESP_ERR_INVALID_ARG;
    bool pending_used=false;
    tv_connection_t next=*out;esp_err_t e=recover(origin,NULL,&next,esp_timer_get_time()+(int64_t)timeout_ms*1000,true,&pending_used);
    if(e==ESP_OK && pending_used)e=pairing_client_committed(origin);
    if(e==ESP_OK)*out=next;
    memset(&next,0,sizeof next);return e;
}
esp_err_t pairing_client_committed(const char *origin) {
    binding_t b;esp_err_t e=read_binding("pending_v1",&b);
    bool erase=e==ESP_OK && !strcmp(origin,b.origin);
    if(erase)e=write_binding("active_v1",&b);
    memset(&b,0,sizeof b);
    if(e==ESP_ERR_NOT_FOUND || (e==ESP_OK && !erase))return ESP_OK;
    if(e!=ESP_OK)return e;
    // State and active are already committed. Cleanup is redundant: reporting
    // failure after deleting pending would make a one-time-code retry redeem
    // again instead of finishing the successfully saved configuration.
    nvs_handle_t h;e=nvs_open("ptv_auth",NVS_READWRITE,&h);if(e!=ESP_OK)return ESP_OK;
    e=nvs_erase_key(h,"pending_v1");
    if(e==ESP_OK){esp_err_t old=nvs_erase_key(h,"pending");if(old!=ESP_OK && old!=ESP_ERR_NVS_NOT_FOUND)e=old;}
    if(e==ESP_OK)e=nvs_commit(h);
    nvs_close(h);return ESP_OK;
}
