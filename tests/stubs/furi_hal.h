#pragma once
#include "furi.h"
typedef struct { int unused; } FuriHalUsbInterface;
extern FuriHalUsbInterface usb_hid;
bool furi_hal_hid_is_connected(void);
bool furi_hal_hid_kb_press(uint16_t key);
bool furi_hal_hid_kb_release(uint16_t key);
bool furi_hal_hid_kb_release_all(void);
FuriHalUsbInterface* furi_hal_usb_get_config(void);
void furi_hal_usb_unlock(void);
bool furi_hal_usb_set_config(FuriHalUsbInterface* config, void* context);
#define HID_KEYBOARD_RETURN 0x28
#define HID_KEYBOARD_TAB 0x2b
