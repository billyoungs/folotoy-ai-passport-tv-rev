#include "tv_config.h"
#include <string.h>
#include <stdio.h>
bool tv_host_valid(const char *host,size_t cap) {
    if(!host || !memchr(host,0,cap) || !host[0])return false;
    size_t label=0;
    for(size_t i=0;host[i];i++){
        char c=host[i];
        if(c=='.'){
            if(!label || host[i-1]=='-')return false;
            label=0;continue;
        }
        if(!((c>='a'&&c<='z')||(c>='A'&&c<='Z')||(c>='0'&&c<='9')||c=='-') ||
           (!label && c=='-') || ++label>63)return false;
    }
    return label && host[strlen(host)-1]!='-';
}
bool tv_origin_from_input(const char *input,uint16_t port,const char *default_origin,char out[TV_ORIGIN_CAP]) {
    if(!input || !out || strlen(input)>=TV_ORIGIN_CAP)return false;
    if(!input[0]){
        if(!default_origin || !default_origin[0])return false;
        input=default_origin;
    }
    bool https=!strncmp(input,"https://",8),http=!strncmp(input,"http://",7);
    const char *host=input+(https?8:http?7:0),*colon=strchr(host,':');
    size_t n=colon?(size_t)(colon-host):strlen(host);char name[TV_ORIGIN_CAP];
    if(!n || n>=sizeof name)return false;
    memcpy(name,host,n);name[n]=0;
    if(!tv_host_valid(name,sizeof name))return false;
    if(colon){
        const char *p=colon+1;unsigned value=0;if(!*p)return false;
        for(;*p;p++){if(*p<'0'||*p>'9')return false;value=value*10+(unsigned)(*p-'0');if(value>65535)return false;}
        if(!value)return false;
        port=(uint16_t)value;
    }else if(https || http)port=https?443:80;
    if(!port)return false;
    for(size_t i=0;i<n;i++)if(name[i]>='A'&&name[i]<='Z')name[i]+='a'-'A';
    bool standard=port==(https?443:80);
    int written=standard?snprintf(out,TV_ORIGIN_CAP,"%s://%s",https?"https":"http",name):
        snprintf(out,TV_ORIGIN_CAP,"%s://%s:%u",https?"https":"http",name,(unsigned)port);
    return written>0 && written<TV_ORIGIN_CAP;
}
static bool printable(const char *p, size_t cap) {
    const char *end=memchr(p,0,cap);
    if(!end)return false;
    size_t n=(size_t)(end-p);
    for(size_t i=0;i<n;i++)if((unsigned char)p[i]<32 || (unsigned char)p[i]==127)return false;
    return true;
}
bool tv_connection_valid(const tv_connection_t *c) {
    if(!c || !printable(c->ssid,sizeof c->ssid) || !c->ssid[0] ||
       !printable(c->password,sizeof c->password) || !printable(c->host,sizeof c->host) ||
       !printable(c->token,sizeof c->token) || !c->port)return false;
    size_t n=strlen(c->password);
    if(n && n<8)return false;
    if(n==64)for(size_t i=0;i<n;i++)if(!strchr("0123456789abcdefABCDEF",c->password[i]))return false;
    if(!tv_host_valid(c->host,sizeof c->host))return false;
    n=strlen(c->token);
    if(n<16)return false;
    for(size_t i=0;i<n;i++)if((unsigned char)c->token[i]<33 || (unsigned char)c->token[i]>126)return false;
    return true;
}
