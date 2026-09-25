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

int main(void) {
    TtfReceiver rx;
    ttf_rx_reset(&rx);
    assert(feed(&rx, "TTF?\n") == TtfRxHello);
    assert(rx.header_len == 0);
    assert(feed(&rx, "TTF1 9 cbf43926\n1234") == TtfRxMore);
    assert(feed(&rx, "56789") == TtfRxComplete);
    assert(strcmp(rx.text, "123456789") == 0);
    ttf_rx_reset(&rx);
    assert(feed(&rx, "TTF1 9 cbf43926\n123456788") == TtfRxChecksum);
    ttf_rx_reset(&rx);
    assert(feed(&rx, "TTF1 256 00000000\n") == TtfRxTooLong);
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
    char preview[64];
    assert(ttf_preview("a\nb\t[CTRL+c]", 0, preview, sizeof(preview)) == 22);
    assert(strcmp(preview, "a[ENTER]b[TAB][CTRL+c]") == 0);
    ttf_preview("a\nb\t[CTRL+c]", 7, preview, sizeof(preview));
    assert(strcmp(preview, "]b[TAB][CTRL+c]") == 0);
    puts("Protocol/CRC/length/incomplete/preview checks passed");
    return 0;
}
