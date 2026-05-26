/*
 * TransTheFlip — BLE Remote Bad USB
 * Copyright (C) 2026 Romain Champliau
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <https://www.gnu.org/licenses/>.
 */
#include "trans_the_flip_hid.h"

#include <furi.h>
#include <furi_hal.h>
#include <furi_hal_usb.h>
#include <furi_hal_usb_hid.h>

#include <string.h>
#include <stdlib.h>
#include <ctype.h>

// ============================================================
// Délais entre frappes (ms)
// ============================================================
#define TTF_KEY_PRESS_MS   12   // Temps touche enfoncée
#define TTF_KEY_RELEASE_MS 8    // Délai après relâchement

// ============================================================
// Config USB sauvegardée pour restauration
// ============================================================
static FuriHalUsbInterface* s_prev_usb_config = NULL;

// ============================================================
// Table ASCII → HID (layout QWERTY US)
// Indexée par (ascii_code - 32), couvre les codes 32..126
// Chaque entrée : {hid_keycode, modifier_byte}
//   modifier_byte 0x02 = LEFT_SHIFT
// ============================================================
typedef struct {
    uint8_t key;
    uint8_t mod;
} AsciiHidEntry;

/*
 * Pour mémoire :
 *   HID_KEYBOARD_A = 0x04 ... HID_KEYBOARD_Z = 0x1D
 *   HID_KEYBOARD_1 = 0x1E ... HID_KEYBOARD_9 = 0x26, 0 = 0x27
 *   -/_  = 0x2D,  =/+  = 0x2E,  [/{  = 0x2F,  ]/}  = 0x30
 *   \/|  = 0x31,  ;/:  = 0x33,  '/"  = 0x34,  `/~  = 0x35
 *   ,/<  = 0x36,  ./>  = 0x37,  //?  = 0x38
 */
static const AsciiHidEntry ascii_hid_table[95] = {
    /* 32 ' '  */ {0x2C, 0x00},
    /* 33 '!'  */ {0x1E, 0x02},
    /* 34 '"'  */ {0x34, 0x02},
    /* 35 '#'  */ {0x20, 0x02},
    /* 36 '$'  */ {0x21, 0x02},
    /* 37 '%'  */ {0x22, 0x02},
    /* 38 '&'  */ {0x24, 0x02},
    /* 39 '\'' */ {0x34, 0x00},
    /* 40 '('  */ {0x26, 0x02},
    /* 41 ')'  */ {0x27, 0x02},
    /* 42 '*'  */ {0x25, 0x02},
    /* 43 '+'  */ {0x2E, 0x02},
    /* 44 ','  */ {0x36, 0x00},
    /* 45 '-'  */ {0x2D, 0x00},
    /* 46 '.'  */ {0x37, 0x00},
    /* 47 '/'  */ {0x38, 0x00},
    /* 48 '0'  */ {0x27, 0x00},
    /* 49 '1'  */ {0x1E, 0x00},
    /* 50 '2'  */ {0x1F, 0x00},
    /* 51 '3'  */ {0x20, 0x00},
    /* 52 '4'  */ {0x21, 0x00},
    /* 53 '5'  */ {0x22, 0x00},
    /* 54 '6'  */ {0x23, 0x00},
    /* 55 '7'  */ {0x24, 0x00},
    /* 56 '8'  */ {0x25, 0x00},
    /* 57 '9'  */ {0x26, 0x00},
    /* 58 ':'  */ {0x33, 0x02},
    /* 59 ';'  */ {0x33, 0x00},
    /* 60 '<'  */ {0x36, 0x02},
    /* 61 '='  */ {0x2E, 0x00},
    /* 62 '>'  */ {0x37, 0x02},
    /* 63 '?'  */ {0x38, 0x02},
    /* 64 '@'  */ {0x1F, 0x02},
    /* 65 'A'  */ {0x04, 0x02},
    /* 66 'B'  */ {0x05, 0x02},
    /* 67 'C'  */ {0x06, 0x02},
    /* 68 'D'  */ {0x07, 0x02},
    /* 69 'E'  */ {0x08, 0x02},
    /* 70 'F'  */ {0x09, 0x02},
    /* 71 'G'  */ {0x0A, 0x02},
    /* 72 'H'  */ {0x0B, 0x02},
    /* 73 'I'  */ {0x0C, 0x02},
    /* 74 'J'  */ {0x0D, 0x02},
    /* 75 'K'  */ {0x0E, 0x02},
    /* 76 'L'  */ {0x0F, 0x02},
    /* 77 'M'  */ {0x10, 0x02},
    /* 78 'N'  */ {0x11, 0x02},
    /* 79 'O'  */ {0x12, 0x02},
    /* 80 'P'  */ {0x13, 0x02},
    /* 81 'Q'  */ {0x14, 0x02},
    /* 82 'R'  */ {0x15, 0x02},
    /* 83 'S'  */ {0x16, 0x02},
    /* 84 'T'  */ {0x17, 0x02},
    /* 85 'U'  */ {0x18, 0x02},
    /* 86 'V'  */ {0x19, 0x02},
    /* 87 'W'  */ {0x1A, 0x02},
    /* 88 'X'  */ {0x1B, 0x02},
    /* 89 'Y'  */ {0x1C, 0x02},
    /* 90 'Z'  */ {0x1D, 0x02},
    /* 91 '['  */ {0x2F, 0x00},
    /* 92 '\'  */ {0x31, 0x00},
    /* 93 ']'  */ {0x30, 0x00},
    /* 94 '^'  */ {0x23, 0x02},
    /* 95 '_'  */ {0x2D, 0x02},
    /* 96 '`'  */ {0x35, 0x00},
    /* 97 'a'  */ {0x04, 0x00},
    /* 98 'b'  */ {0x05, 0x00},
    /* 99 'c'  */ {0x06, 0x00},
    /* 100 'd' */ {0x07, 0x00},
    /* 101 'e' */ {0x08, 0x00},
    /* 102 'f' */ {0x09, 0x00},
    /* 103 'g' */ {0x0A, 0x00},
    /* 104 'h' */ {0x0B, 0x00},
    /* 105 'i' */ {0x0C, 0x00},
    /* 106 'j' */ {0x0D, 0x00},
    /* 107 'k' */ {0x0E, 0x00},
    /* 108 'l' */ {0x0F, 0x00},
    /* 109 'm' */ {0x10, 0x00},
    /* 110 'n' */ {0x11, 0x00},
    /* 111 'o' */ {0x12, 0x00},
    /* 112 'p' */ {0x13, 0x00},
    /* 113 'q' */ {0x14, 0x00},
    /* 114 'r' */ {0x15, 0x00},
    /* 115 's' */ {0x16, 0x00},
    /* 116 't' */ {0x17, 0x00},
    /* 117 'u' */ {0x18, 0x00},
    /* 118 'v' */ {0x19, 0x00},
    /* 119 'w' */ {0x1A, 0x00},
    /* 120 'x' */ {0x1B, 0x00},
    /* 121 'y' */ {0x1C, 0x00},
    /* 122 'z' */ {0x1D, 0x00},
    /* 123 '{' */ {0x2F, 0x02},
    /* 124 '|' */ {0x31, 0x02},
    /* 125 '}' */ {0x30, 0x02},
    /* 126 '~' */ {0x35, 0x02},
};

// ============================================================
// Table des touches spéciales nommées
// ============================================================
typedef struct {
    const char* name;
    uint8_t     key;
    uint8_t     mod; // généralement 0 (pas de modificateur)
} SpecialKeyEntry;

static const SpecialKeyEntry s_special_keys[] = {
    {"ENTER",        0x28, 0x00},
    {"RETURN",       0x28, 0x00},
    {"TAB",          0x2B, 0x00},
    {"ESC",          0x29, 0x00},
    {"ESCAPE",       0x29, 0x00},
    {"BACKSPACE",    0x2A, 0x00},
    {"DELETE",       0x4C, 0x00},
    {"DEL",          0x4C, 0x00},
    {"INSERT",       0x49, 0x00},
    {"INS",          0x49, 0x00},
    {"HOME",         0x4A, 0x00},
    {"END",          0x4D, 0x00},
    {"PAGEUP",       0x4B, 0x00},
    {"PGUP",         0x4B, 0x00},
    {"PAGEDOWN",     0x4E, 0x00},
    {"PGDN",         0x4E, 0x00},
    {"UP",           0x52, 0x00},
    {"DOWN",         0x51, 0x00},
    {"LEFT",         0x50, 0x00},
    {"RIGHT",        0x4F, 0x00},
    {"SPACE",        0x2C, 0x00},
    {"F1",           0x3A, 0x00},
    {"F2",           0x3B, 0x00},
    {"F3",           0x3C, 0x00},
    {"F4",           0x3D, 0x00},
    {"F5",           0x3E, 0x00},
    {"F6",           0x3F, 0x00},
    {"F7",           0x40, 0x00},
    {"F8",           0x41, 0x00},
    {"F9",           0x42, 0x00},
    {"F10",          0x43, 0x00},
    {"F11",          0x44, 0x00},
    {"F12",          0x45, 0x00},
    {"PRINTSCREEN",  0x46, 0x00},
    {"SCROLLLOCK",   0x47, 0x00},
    {"PAUSE",        0x48, 0x00},
    {"CAPSLOCK",     0x39, 0x00},
    {"NUMLOCK",      0x53, 0x00},
    {NULL,           0x00, 0x00},
};

// ============================================================
// Table des modificateurs
// Les valeurs correspondent au modifier_byte USB HID,
// stocké dans l'octet de poids fort du uint16_t passé à
// furi_hal_hid_kb_press()  →  (mod_byte << 8) | keycode
// ============================================================
typedef struct {
    const char* name;
    uint8_t     mod; // USB HID modifier byte bit
} ModifierEntry;

static const ModifierEntry s_modifiers[] = {
    {"CTRL",    0x01},
    {"CONTROL", 0x01},
    {"SHIFT",   0x02},
    {"ALT",     0x04},
    {"GUI",     0x08},
    {"WIN",     0x08},
    {"WINDOWS", 0x08},
    {"META",    0x08},
    {NULL,      0x00},
};

// ============================================================
// Utilitaires internes
// ============================================================

/** Compare deux chaînes sans tenir compte de la casse (ASCII). */
static bool ttf_str_ieq(const char* a, const char* b) {
    while(*a && *b) {
        if(toupper((unsigned char)*a) != toupper((unsigned char)*b)) return false;
        a++;
        b++;
    }
    return (*a == '\0' && *b == '\0');
}

/** Compare n chars sans tenir compte de la casse. */
static bool ttf_strn_ieq(const char* a, const char* b, size_t n) {
    for(size_t i = 0; i < n; i++) {
        if(toupper((unsigned char)a[i]) != toupper((unsigned char)b[i])) return false;
        if(a[i] == '\0') return true;
    }
    return true;
}

static uint16_t make_hid(uint8_t mod, uint8_t key) {
    return ((uint16_t)mod << 8) | key;
}

/** Presse et relâche une touche HID. */
static void press_and_release(uint16_t hid_key) {
    furi_hal_hid_kb_press(hid_key);
    furi_delay_ms(TTF_KEY_PRESS_MS);
    furi_hal_hid_kb_release(hid_key);
    furi_delay_ms(TTF_KEY_RELEASE_MS);
}

/** Envoie un seul caractère ASCII imprimable. */
static void send_ascii_char(char c) {
    uint8_t idx = (uint8_t)c;
    if(idx < 32 || idx > 126) return; // non imprimable
    const AsciiHidEntry* e = &ascii_hid_table[idx - 32];
    if(e->key == 0x00) return;
    press_and_release(make_hid(e->mod, e->key));
}

/** Cherche une touche spéciale par nom (insensible à la casse). */
static const SpecialKeyEntry* find_special_key(const char* name) {
    for(int i = 0; s_special_keys[i].name != NULL; i++) {
        if(ttf_str_ieq(s_special_keys[i].name, name)) {
            return &s_special_keys[i];
        }
    }
    return NULL;
}

/** Cherche un modificateur par nom, retourne son bit (0 si non trouvé). */
static uint8_t find_modifier(const char* name) {
    for(int i = 0; s_modifiers[i].name != NULL; i++) {
        if(ttf_str_ieq(s_modifiers[i].name, name)) {
            return s_modifiers[i].mod;
        }
    }
    return 0;
}

/**
 * Traite un tag du type "CTRL+SHIFT+c" ou "WIN+r" ou "ALT+F4".
 * Tous les tokens sauf le dernier sont des modificateurs.
 * Le dernier token est la touche.
 */
static void handle_combo_tag(const char* tag) {
    // Copier localement pour modification
    char buf[32];
    strncpy(buf, tag, sizeof(buf) - 1);
    buf[sizeof(buf) - 1] = '\0';

    // Découper par '+'
    const char* parts[8] = {NULL};
    int num_parts = 0;
    char* p = buf;
    parts[num_parts++] = p;
    while(*p && num_parts < (int)(sizeof(parts) / sizeof(parts[0]))) {
        if(*p == '+') {
            *p = '\0';
            parts[num_parts++] = p + 1;
        }
        p++;
    }

    // Accumuler modificateurs (tous sauf dernier)
    uint8_t total_mod = 0;
    for(int i = 0; i < num_parts - 1; i++) {
        uint8_t m = find_modifier(parts[i]);
        total_mod |= m;
    }

    // Dernier élément = la touche
    const char* key_str = parts[num_parts - 1];

    // D'abord chercher dans les touches spéciales nommées
    const SpecialKeyEntry* sk = find_special_key(key_str);
    if(sk) {
        press_and_release(make_hid(total_mod, sk->key));
        return;
    }

    // Sinon, si c'est un seul caractère lettre/chiffre
    if(key_str[1] == '\0') {
        char c = key_str[0];
        // Lettre : hid = 0x04 + (lower - 'a')
        char cl = (char)tolower((unsigned char)c);
        if(cl >= 'a' && cl <= 'z') {
            press_and_release(make_hid(total_mod, 0x04 + (uint8_t)(cl - 'a')));
            return;
        }
        // Chiffre ou ponctuation depuis table ASCII
        if((uint8_t)c >= 32 && (uint8_t)c <= 126) {
            const AsciiHidEntry* e = &ascii_hid_table[(uint8_t)c - 32];
            if(e->key != 0) {
                press_and_release(make_hid(total_mod | e->mod, e->key));
                return;
            }
        }
    }
}

/**
 * Traite le contenu d'un tag [TAG] :
 *  - [DELAY:500]   → pause 500 ms
 *  - [CTRL+c]      → combinaison
 *  - [ENTER]       → touche spéciale
 *  - [a]           → caractère (fallback)
 */
static void handle_tag(const char* tag) {
    // DELAY:<ms>
    if(ttf_strn_ieq(tag, "DELAY:", 6)) {
        int ms = atoi(tag + 6);
        if(ms > 0 && ms <= 30000) {
            furi_delay_ms((uint32_t)ms);
        }
        return;
    }

    // Combo (contient '+')
    if(strchr(tag, '+') != NULL) {
        handle_combo_tag(tag);
        return;
    }

    // Touche spéciale nommée
    const SpecialKeyEntry* sk = find_special_key(tag);
    if(sk) {
        press_and_release(make_hid(sk->mod, sk->key));
        return;
    }

    // Caractère unique (fallback)
    if(tag[0] != '\0' && tag[1] == '\0') {
        send_ascii_char(tag[0]);
    }
}

// ============================================================
// API publique
// ============================================================

bool ttf_hid_init(void) {
    // Sauvegarder la config USB courante
    s_prev_usb_config = furi_hal_usb_get_config();

    // Déverrouiller et basculer en HID keyboard
    furi_hal_usb_unlock();
    bool ok = furi_hal_usb_set_config(&usb_hid, NULL);
    if(!ok) {
        s_prev_usb_config = NULL;
        return false;
    }

    // Laisser le temps au PC cible de reconnaître le périphérique HID
    furi_delay_ms(500);
    return true;
}

void ttf_hid_deinit(void) {
    furi_hal_hid_kb_release_all();

    if(s_prev_usb_config != NULL) {
        furi_hal_usb_set_config(s_prev_usb_config, NULL);
        s_prev_usb_config = NULL;
    }
}

bool ttf_hid_send_string(const char* text, size_t len) {
    if(!text || len == 0) return false;

    size_t i = 0;
    while(i < len) {
        char c = text[i];

        if(c == '[') {
            // Chercher le ']' correspondant
            size_t tag_start = i + 1;
            size_t tag_end   = tag_start;
            while(tag_end < len && text[tag_end] != ']') {
                tag_end++;
            }

            if(tag_end < len && text[tag_end] == ']') {
                size_t tag_len = tag_end - tag_start;
                if(tag_len > 0 && tag_len < 30) {
                    char tag_buf[31] = {0};
                    memcpy(tag_buf, text + tag_start, tag_len);
                    handle_tag(tag_buf);
                } else if(tag_len == 0) {
                    // [] vide → envoyer '[' littéral
                    send_ascii_char('[');
                }
                i = tag_end + 1;
            } else {
                // Pas de ']' trouvé → '[' littéral
                send_ascii_char('[');
                i++;
            }
        } else {
            send_ascii_char(c);
            i++;
        }
    }

    furi_hal_hid_kb_release_all();
    return true;
}
