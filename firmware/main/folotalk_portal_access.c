#include "folotalk_portal_access.h"
#include <stddef.h>
#include <string.h>
#ifndef ESP_PLATFORM
#include <netinet/in.h>
#endif

bool ft_portal_address_allowed(const struct sockaddr *address, socklen_t length, uint32_t expected_ipv4) {
    if (!address || length < offsetof(struct sockaddr, sa_family) + sizeof address->sa_family) return false;
    if (address->sa_family == AF_INET) {
        if (length < sizeof(struct sockaddr_in)) return false;
        struct sockaddr_in ipv4;
        memcpy(&ipv4, address, sizeof ipv4);
        return ipv4.sin_addr.s_addr == expected_ipv4;
    }
#ifdef AF_INET6
    if (address->sa_family == AF_INET6) {
        if (length < sizeof(struct sockaddr_in6)) return false;
        struct sockaddr_in6 ipv6;
        memcpy(&ipv6, address, sizeof ipv6);
        const unsigned char mapped_prefix[12] = {0,0,0,0,0,0,0,0,0,0,0xff,0xff};
        const unsigned char *bytes = (const unsigned char *)&ipv6.sin6_addr;
        if (memcmp(bytes, mapped_prefix, sizeof mapped_prefix)) return false;
        uint32_t ipv4;
        memcpy(&ipv4, bytes + sizeof mapped_prefix, sizeof ipv4);
        return ipv4 == expected_ipv4;
    }
#endif
    return false;
}

bool ft_portal_socket_allowed(int fd, uint32_t expected_ipv4) {
    // ESP-IDF httpd uses an IPv6 listener when IPv6 is enabled. lwIP returns
    // IPv4-mapped sockaddr_in6 values even for phones opening an IPv4 URL.
    struct sockaddr_storage local = {0};
    socklen_t length = sizeof local;
    if (getsockname(fd, (struct sockaddr *)&local, &length) != 0) return false;
    return ft_portal_address_allowed((const struct sockaddr *)&local, length, expected_ipv4);
}

