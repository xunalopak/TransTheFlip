#include "trans_the_flip_view.h"
#include "trans_the_flip.h"

#include <furi.h>
#include <gui/canvas.h>
#include <string.h>
#include <stdio.h>

// ============================================================
// Dimensions de l'écran Flipper Zero : 128 × 64 px
// Layout :
//   y=0..12   : en-tête  (titre + séparateur)
//   y=13..52  : contenu  (zone libre)
//   y=53..63  : pied     (séparateur + hints boutons)
// ============================================================
#define SCREEN_W       128
#define SCREEN_H       64
#define HEADER_H       13
#define FOOTER_Y       53
#define CONTENT_TOP    (HEADER_H + 2)
#define CONTENT_MID_Y  33          // Milieu de la zone contenu

// Largeur affichable en FontSecondary ≈ 21 chars par ligne
#define DISPLAY_COLS   21

// ============================================================
// Helpers internes
// ============================================================

/** Tronque str dans buf à max_len chars (ajoute "..." si nécessaire). */
static void truncate_str(const char* str, char* buf, size_t max_len) {
    size_t len = strlen(str);
    if(len <= max_len) {
        strncpy(buf, str, max_len + 1);
    } else {
        strncpy(buf, str, max_len - 3);
        buf[max_len - 3] = '.';
        buf[max_len - 2] = '.';
        buf[max_len - 1] = '.';
        buf[max_len]     = '\0';
    }
}

/** Dessine l'en-tête commun (titre + ligne de séparation). */
static void draw_header(Canvas* canvas) {
    canvas_set_font(canvas, FontPrimary);
    canvas_draw_str_aligned(canvas, SCREEN_W / 2, 7, AlignCenter, AlignCenter, "TransTheFlip");
    canvas_draw_line(canvas, 0, HEADER_H, SCREEN_W - 1, HEADER_H);
}

/** Dessine le pied de page avec les hints de boutons. */
static void draw_footer(Canvas* canvas, const char* left, const char* right) {
    canvas_draw_line(canvas, 0, FOOTER_Y, SCREEN_W - 1, FOOTER_Y);
    canvas_set_font(canvas, FontSecondary);
    if(left) {
        canvas_draw_str_aligned(canvas, 2, SCREEN_H - 3, AlignLeft, AlignBottom, left);
    }
    if(right) {
        canvas_draw_str_aligned(canvas, SCREEN_W - 2, SCREEN_H - 3, AlignRight, AlignBottom, right);
    }
}

// ============================================================
// Rendu par état
// ============================================================

static void draw_waiting_bt(Canvas* canvas) {
    draw_header(canvas);

    canvas_set_font(canvas, FontSecondary);
    canvas_draw_str_aligned(canvas, SCREEN_W / 2, CONTENT_MID_Y - 6,
                            AlignCenter, AlignCenter, "Waiting for BLE...");
    canvas_draw_str_aligned(canvas, SCREEN_W / 2, CONTENT_MID_Y + 6,
                            AlignCenter, AlignCenter, "Connect from PC");

    draw_footer(canvas, NULL, "Back:Exit");
}

static void draw_connected(Canvas* canvas) {
    draw_header(canvas);

    canvas_set_font(canvas, FontSecondary);
    canvas_draw_str_aligned(canvas, SCREEN_W / 2, CONTENT_MID_Y - 6,
                            AlignCenter, AlignCenter, "BLE Connected!");
    canvas_draw_str_aligned(canvas, SCREEN_W / 2, CONTENT_MID_Y + 6,
                            AlignCenter, AlignCenter, "Send text from client");

    draw_footer(canvas, NULL, "Back:Exit");
}

static void draw_text_received(Canvas* canvas, const char* text, size_t text_len) {
    draw_header(canvas);

    canvas_set_font(canvas, FontSecondary);
    canvas_draw_str(canvas, 2, CONTENT_TOP + 7, "Received:");

    // Afficher jusqu'à 3 lignes du texte (21 chars max par ligne)
    char line_buf[DISPLAY_COLS + 4]; // +4 pour '...\0'
    int y = CONTENT_TOP + 17;
    size_t start = 0;
    const int max_lines = 3;

    for(int line = 0; line < max_lines && start < text_len; line++) {
        bool is_last = (line == max_lines - 1);
        size_t remaining = text_len - start;

        if(remaining <= DISPLAY_COLS) {
            strncpy(line_buf, text + start, remaining);
            line_buf[remaining] = '\0';
            start += remaining;
        } else if(is_last) {
            // Dernière ligne visible, tronquer avec "..."
            strncpy(line_buf, text + start, DISPLAY_COLS - 3);
            line_buf[DISPLAY_COLS - 3] = '.';
            line_buf[DISPLAY_COLS - 2] = '.';
            line_buf[DISPLAY_COLS - 1] = '.';
            line_buf[DISPLAY_COLS]     = '\0';
            start = text_len;
        } else {
            strncpy(line_buf, text + start, DISPLAY_COLS);
            line_buf[DISPLAY_COLS] = '\0';
            start += DISPLAY_COLS;
        }
        canvas_draw_str(canvas, 2, y, line_buf);
        y += 8;
    }

    draw_footer(canvas, "Back:Skip", "OK:Send");
}

static void draw_sending(Canvas* canvas) {
    draw_header(canvas);

    canvas_set_font(canvas, FontSecondary);
    canvas_draw_str_aligned(canvas, SCREEN_W / 2, CONTENT_MID_Y - 4,
                            AlignCenter, AlignCenter, "Sending keystrokes...");
    canvas_draw_str_aligned(canvas, SCREEN_W / 2, CONTENT_MID_Y + 8,
                            AlignCenter, AlignCenter, "Do not unplug USB");
    // Petite animation : carré clignotant au centre
    canvas_draw_box(canvas, 58, CONTENT_MID_Y + 18, 12, 4);
}

static void draw_done(Canvas* canvas) {
    draw_header(canvas);

    canvas_set_font(canvas, FontPrimary);
    canvas_draw_str_aligned(canvas, SCREEN_W / 2, CONTENT_MID_Y - 4,
                            AlignCenter, AlignCenter, "Done!");
    canvas_set_font(canvas, FontSecondary);
    canvas_draw_str_aligned(canvas, SCREEN_W / 2, CONTENT_MID_Y + 10,
                            AlignCenter, AlignCenter, "Returning...");
}

static void draw_error(Canvas* canvas, const char* error_msg) {
    draw_header(canvas);

    canvas_set_font(canvas, FontSecondary);
    canvas_draw_str_aligned(canvas, SCREEN_W / 2, CONTENT_MID_Y - 8,
                            AlignCenter, AlignCenter, "Error:");

    char buf[DISPLAY_COLS + 4];
    truncate_str(error_msg ? error_msg : "Unknown error", buf, DISPLAY_COLS);
    canvas_draw_str_aligned(canvas, SCREEN_W / 2, CONTENT_MID_Y + 4,
                            AlignCenter, AlignCenter, buf);

    draw_footer(canvas, "Back:Return", NULL);
}

// ============================================================
// Callbacks publics
// ============================================================

void ttf_view_draw_callback(Canvas* canvas, void* context) {
    TransTheFlipApp* app = (TransTheFlipApp*)context;

    // Snapshot de l'état sous mutex pour éviter les races avec le main thread
    furi_mutex_acquire(app->mutex, FuriWaitForever);
    AppState    state          = app->state;
    size_t      text_len       = app->text_len;
    char text_copy[TTF_TEXT_BUFFER_SIZE];
    char err_copy[TTF_ERROR_MSG_SIZE];
    strncpy(text_copy, app->received_text, sizeof(text_copy) - 1);
    text_copy[sizeof(text_copy) - 1] = '\0';
    strncpy(err_copy, app->error_msg, sizeof(err_copy) - 1);
    err_copy[sizeof(err_copy) - 1] = '\0';
    furi_mutex_release(app->mutex);

    canvas_clear(canvas);

    switch(state) {
    case AppStateWaitingBT:
        draw_waiting_bt(canvas);
        break;
    case AppStateConnected:
        draw_connected(canvas);
        break;
    case AppStateTextReceived:
        draw_text_received(canvas, text_copy, text_len);
        break;
    case AppStateSending:
        draw_sending(canvas);
        break;
    case AppStateDone:
        draw_done(canvas);
        break;
    case AppStateError:
        draw_error(canvas, err_copy);
        break;
    default:
        break;
    }
}

void ttf_view_input_callback(InputEvent* event, void* context) {
    TransTheFlipApp* app = (TransTheFlipApp*)context;
    AppEvent ev;
    ev.type  = EventTypeInput;
    ev.input = *event;
    furi_message_queue_put(app->event_queue, &ev, FuriWaitForever);
}
