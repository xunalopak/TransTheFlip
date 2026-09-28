// Compile the production sender with a fake USB device; no keystrokes reach a PC.
#include "../trans_the_flip_hid.h"
#include "../trans_the_flip_protocol.h"
#include "stubs/furi_hal.h"
#include "stubs/storage/storage.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

struct File { int unused; };
static unsigned elapsed, presses, releases, release_all;
static unsigned cancel_after, unplug_after;
static uint16_t last_key;
static bool connected = true, fail_press;
static const char* file_text;
static size_t file_pos;
static File fake_file;

FuriHalUsbInterface usb_hid;
void* furi_record_open(const char* record) { (void)record; return file_text ? (void*)1 : NULL; }
void furi_record_close(const char* record) { (void)record; }
File* storage_file_alloc(Storage* storage) { (void)storage; return &fake_file; }
bool storage_file_open(File* file, const char* path, int access, int mode) {
    (void)file; (void)path; (void)access; (void)mode; return file_text != NULL;
}
uint16_t storage_file_read(File* file, void* buffer, uint16_t length) {
    (void)file;
    size_t remaining = strlen(file_text) - file_pos;
    if(remaining > length) remaining = length;
    memcpy(buffer, file_text + file_pos, remaining);
    file_pos += remaining;
    return (uint16_t)remaining;
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
    file_text = NULL;
    file_pos = 0;
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
    file_text = "a[ENTER]b";
    file_pos = 0;
    reset(8);
    file_text = "a[ENTER]b";
    assert(ttf_hid_send_file("/ext/apps_data/trans_the_flip/payload.tmp", strlen(file_text)));
    assert(presses == 3 && ttf_hid_progress() == strlen(file_text));
    char long_text[TTF_TEXT_BUFFER_SIZE];
    memset(long_text, 'x', sizeof(long_text) - 1);
    long_text[sizeof(long_text) - 1] = '\0';
    reset(8);
    assert(ttf_hid_send_string(long_text, sizeof(long_text) - 1));
    assert(presses == 4096 && releases == 4096 && release_all == 1);
    assert(ttf_hid_progress() == 4096);
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
    puts("HID 4096-byte/multiline/speed/progress/cancellation/USB failure checks passed");
    return 0;
}
