#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define TTF_TEXT_BUFFER_SIZE 256
#define TTF_RX_TIMEOUT_MS 5000

typedef enum {
    TtfRxMore, TtfRxHello, TtfRxComplete, TtfRxProtocol, TtfRxTooLong,
    TtfRxChecksum, TtfRxUnsupported,
} TtfRxResult;

typedef struct {
    char header[32];
    size_t header_len;
    char text[TTF_TEXT_BUFFER_SIZE];
    size_t length;
    size_t expected;
    uint32_t checksum;
    uint32_t crc;
} TtfReceiver;

static inline void ttf_rx_reset(TtfReceiver* rx) {
    memset(rx, 0, sizeof(*rx));
}

// Expand control characters for a readable, scrollable preview without allocating
// a second full-size buffer. Returns the total number of displayed characters.
static inline size_t ttf_preview(const char* text, size_t offset, char* out, size_t capacity) {
    size_t total = 0, written = 0;
    for(size_t i = 0; text[i]; i++) {
        char literal[2] = {text[i], 0};
        const char* token = text[i] == '\n' ? "[ENTER]" : text[i] == '\t' ? "[TAB]" :
            text[i] == '\r' ? "" : literal;
        for(size_t j = 0; token[j]; j++, total++) {
            if(total >= offset && written + 1 < capacity) out[written++] = token[j];
        }
    }
    if(capacity) out[written] = '\0';
    return total;
}

// Wire format: TTF1 <byte length> <CRC32 hex>\n<payload>.
// No text is executable until the entire frame passes validation.
static inline TtfRxResult ttf_rx_feed(TtfReceiver* rx, uint8_t byte) {
    if(rx->expected == 0) {
        if(byte != '\n') {
            if(byte < 32 || byte > 126 || rx->header_len >= sizeof(rx->header) - 1)
                return TtfRxProtocol;
            rx->header[rx->header_len++] = (char)byte;
            return TtfRxMore;
        }
        rx->header[rx->header_len] = '\0';
        if(strcmp(rx->header, "TTF?") == 0) {
            ttf_rx_reset(rx);
            return TtfRxHello;
        }
        unsigned length = 0, crc = 0;
        int end = 0;
        if(sscanf(rx->header, "TTF1 %3u %8x%n", &length, &crc, &end) != 2 ||
           end == 0 || rx->header[end] != '\0' || length == 0)
            return TtfRxProtocol;
        if(length >= TTF_TEXT_BUFFER_SIZE) return TtfRxTooLong;
        rx->expected = length;
        rx->checksum = crc;
        rx->crc = 0xffffffff;
        return TtfRxMore;
    }
    if(byte != '\n' && byte != '\r' && byte != '\t' && (byte < 32 || byte > 126))
        return TtfRxUnsupported;
    rx->text[rx->length++] = (char)byte;
    rx->crc ^= byte;
    for(unsigned bit = 0; bit < 8; bit++)
        rx->crc = (rx->crc >> 1) ^ (0xedb88320u & (0u - (rx->crc & 1u)));
    if(rx->length != rx->expected) return TtfRxMore;
    rx->text[rx->length] = '\0';
    return (rx->crc ^ 0xffffffffu) == rx->checksum ? TtfRxComplete : TtfRxChecksum;
}
