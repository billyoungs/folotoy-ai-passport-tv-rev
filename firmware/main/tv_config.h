#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
typedef struct {
    char ssid[33], password[65], host[64], token[128];
    uint16_t port;
} tv_connection_t;
#define TV_ORIGIN_CAP 129
// Shared by portal and NVS loading; bounded bytes, never logs credentials.
bool tv_connection_valid(const tv_connection_t *c);
bool tv_host_valid(const char *host, size_t cap);
// Bare hosts use port; explicit HTTP(S) origins use their own/default port.
// No paths, userinfo, escapes, IPv6, whitespace or URL query/fragment syntax.
bool tv_origin_from_input(const char *input, uint16_t port, const char *default_origin,
                          char out[TV_ORIGIN_CAP]);
