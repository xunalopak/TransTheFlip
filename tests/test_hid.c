// Compile the production sender with a fake USB device; no keystrokes reach a PC.
#include "../trans_the_flip_hid.h"
#include "stubs/furi_hal.h"
#include "stubs/storage/storage.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

static unsigned elapsed, presses, releases, release_all;
static unsigned cancel_after, unplug_after;
static uint16_t last_key;
static bool connected = true, fail_press;

FuriHalUsbInterface usb_hid;
void* furi_record_open(const char* record) { (void)record; return NULL; }
void furi_record_close(const char* record) { (void)record; }
File* storage_file_alloc(Storage* storage) { (void)storage; return NULL; }
bool storage_file_open(File* file, const char* path, int access, int mode) {
    (void)file; (void)path; (void)access; (void)mode; return false;
}
uint16_t storage_file_read(File* file, void* buffer, uint16_t length) {
    (void)file; (void)buffer; (void)length; return 0;
}
void storage_file_close(File* file) { (void)file; }
void storage_file_free(File* file) { (void)file; }
FuriHalUsbInterface* furi_hal_usb_get_config(void) { return &usb_hid; }
void furi_hal_usb_unlock(void) {}
bool furi_hal_usb_set_config(FuriHalUsbInterface* config, void* context) {
    (void)config; (void)context; return true;
}

void furi_delay_ms(uint32_t ms) {
    elapsed += ms;
    if(cancel_after && elapsed >= cancel_after) ttf_hid_cancel();
    if(unplug_after && elapsed >= unplug_after) connected = false;
}
bool furi_hal_hid_is_connected(void) { return connected; }
bool furi_hal_hid_kb_press(uint16_t key) {
    presses++;
    last_key = key;
    return !fail_press;
}
bool furi_hal_hid_kb_release(uint16_t key) { (void)key; releases++; return connected; }
bool furi_hal_hid_kb_release_all(void) { release_all++; return true; }

static void reset(uint32_t delay) {
    elapsed = presses = releases = release_all = cancel_after = unplug_after = 0;
    connected = true;
    fail_press = false;
    ttf_hid_prepare(delay);
    ttf_hid_reset_layout();
}

int main(void) {
    reset(8);
    assert(ttf_hid_send_string("a\nb\t", 4));
    assert(presses == 4 && releases == 4 && release_all == 1);
    assert(last_key == HID_KEYBOARD_TAB && ttf_hid_progress() == 4);
    assert(elapsed == 80);
    reset(100);
    assert(ttf_hid_send_string("ab", 2));
    assert(elapsed == 224);
    reset(8);
    cancel_after = 30;
    const char* text = "a[DELAY:30000]b";
    assert(!ttf_hid_send_string(text, strlen(text)));
    assert(ttf_hid_cancelled() && elapsed <= 40 && presses == 1 && release_all == 1);
    reset(8);
    cancel_after = 10;
    assert(!ttf_hid_send_string("ab", 2));
    assert(releases == 1 && release_all == 1);
    reset(8);
    unplug_after = 10;
    assert(!ttf_hid_send_string("ab", 2));
    assert(!ttf_hid_cancelled() && presses == 1 && release_all == 1);
    reset(8);
    fail_press = true;
    assert(!ttf_hid_send_string("ab", 2));
    assert(presses == 1 && releases == 1 && release_all == 1);
    puts("HID multiline/speed/progress/cancellation/USB failure checks passed");
    return 0;
}
