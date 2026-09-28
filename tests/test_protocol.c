#include "../trans_the_flip_protocol.h"
#include <assert.h>

static TtfRxResult feed(TtfReceiver* rx, const char* bytes) {
    TtfRxResult result = TtfRxMore;
    for(size_t i = 0; bytes[i]; i++) {
        result = ttf_rx_feed(rx, (uint8_t)bytes[i]);
        if(result != TtfRxMore) break;
    }
    return result;
}

static size_t streamed;
static bool sink(void* context, const uint8_t* data, size_t length) {
    (void)data;
    streamed += length;
    return context != NULL;
}

int main(void) {
    TtfReceiver rx;
    ttf_rx_reset(&rx);
    assert(feed(&rx, "TTF?\n") == TtfRxHello);
    assert(rx.header_len == 0);
    assert(feed(&rx, "TTFEXEC\n") == TtfRxExecute);
    assert(feed(&rx, "TTF1 9 cbf43926\n1234") == TtfRxMore);
    assert(feed(&rx, "56789") == TtfRxComplete);
    assert(strcmp(rx.text, "123456789") == 0);
    ttf_rx_reset(&rx);
    assert(feed(&rx, "TTF1 9 cbf43926\n123456788") == TtfRxChecksum);
    ttf_rx_reset(&rx);
    assert(feed(&rx, "TTF1 65537 00000000\n") == TtfRxTooLong);
    ttf_rx_reset(&rx);
    streamed = 0;
    ttf_rx_set_sink(&rx, sink, &rx);
    assert(feed(&rx, "TTF1 4097 00000000\n") == TtfRxMore);
    for(size_t i = 0; i < 4096; i++) assert(ttf_rx_feed(&rx, 'x') == TtfRxMore);
    assert(ttf_rx_feed(&rx, 'x') == TtfRxChecksum);
    assert(streamed == 4097);
    ttf_rx_reset(&rx);
    assert(feed(&rx, "TTF1 4294967297 00000000\n") == TtfRxProtocol);
    ttf_rx_reset(&rx);
    assert(feed(&rx, "TTF1 0 00000000\n") == TtfRxProtocol);
    ttf_rx_reset(&rx);
    assert(feed(&rx, "legacy command\n") == TtfRxProtocol);
    ttf_rx_reset(&rx);
    assert(feed(&rx, "TTF1 9 cbf43926\n123") == TtfRxMore);
    assert(rx.length == 3 && rx.expected == 9); // Never executable while incomplete.
    assert(ttf_rx_feed(&rx, 0) == TtfRxUnsupported);
    ttf_rx_reset(&rx);
    assert(feed(&rx, "1234567890123456789012345678901234") == TtfRxProtocol);
    ttf_rx_reset(&rx);
    // Known zlib CRC32 of 4096 ASCII x bytes; complete only after the last byte.
    assert(feed(&rx, "TTF1 4096 3e1077c1\n") == TtfRxMore);
    for(size_t i = 0; i < 4095; i++) assert(ttf_rx_feed(&rx, 'x') == TtfRxMore);
    assert(ttf_rx_feed(&rx, 'x') == TtfRxComplete);
    assert(rx.length == 4096 && rx.text[4096] == '\0');
    char tail[64];
    assert(ttf_preview(rx.text, 4095, tail, sizeof(tail)) == 4096);
    assert(strcmp(tail, "x") == 0);
    ttf_rx_reset(&rx);
    assert(feed(&rx, "TTF1 4096 3e1077c1\n") == TtfRxMore);
    for(size_t i = 0; i < 4095; i++) assert(ttf_rx_feed(&rx, 'x') == TtfRxMore);
    assert(ttf_rx_feed(&rx, 'y') == TtfRxChecksum);
    char preview[64];
    assert(ttf_preview("a\nb\t[CTRL+c]", 0, preview, sizeof(preview)) == 22);
    assert(strcmp(preview, "a[ENTER]b[TAB][CTRL+c]") == 0);
    ttf_preview("a\nb\t[CTRL+c]", 7, preview, sizeof(preview));
    assert(strcmp(preview, "]b[TAB][CTRL+c]") == 0);
    puts("Protocol/CRC/length/incomplete/preview checks passed");
    return 0;
}
