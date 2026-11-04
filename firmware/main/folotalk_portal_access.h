#pragma once
#include <stdbool.h>
#include <stdint.h>
#ifdef ESP_PLATFORM
#include "lwip/sockets.h"
#else
#include <sys/socket.h>
#endif

// expected_ipv4 is in network byte order; native IPv6 addresses stay denied.
bool ft_portal_address_allowed(const struct sockaddr *address, socklen_t length, uint32_t expected_ipv4);
bool ft_portal_socket_allowed(int fd, uint32_t expected_ipv4);

