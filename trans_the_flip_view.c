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

// Nombre de lignes d'historique visibles simultanément à l'écran
#define HIST_VISIBLE   3
// Largeur de copie d'une ligne d'historique (> DISPLAY_COLS pour que la
// troncature "..." se déclenche correctement, sans copier 256 o sur la pile)
#define HIST_LINE_LEN  (DISPLAY_COLS + 8)

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

/** Dessine la ligne "Kbd: <layout>" au-dessus du footer. */
static void draw_layout_line(Canvas* canvas, const char* layout) {
    char buf[TTF_LAYOUT_NAME_SIZE + 6];
    snprintf(buf, sizeof(buf), "Kbd: %s", (layout && layout[0]) ? layout : "?");
    canvas_set_font(canvas, FontSecondary);
    canvas_draw_str_aligned(canvas, SCREEN_W / 2, FOOTER_Y - 4,
                            AlignCenter, AlignBottom, buf);
}

static void draw_waiting_bt(Canvas* canvas, const char* layout, size_t history_count) {
    draw_header(canvas);

    canvas_set_font(canvas, FontSecondary);
    canvas_draw_str_aligned(canvas, SCREEN_W / 2, CONTENT_MID_Y - 8,
                            AlignCenter, AlignCenter, "Waiting for BLE...");
    canvas_draw_str_aligned(canvas, SCREEN_W / 2, CONTENT_MID_Y + 2,
                            AlignCenter, AlignCenter,
                            history_count ? "PC connect / Up:Log" : "Connect from PC");

    draw_layout_line(canvas, layout);
}

static void draw_connected(Canvas* canvas, const char* layout, size_t history_count) {
    draw_header(canvas);

    canvas_set_font(canvas, FontSecondary);
    canvas_draw_str_aligned(canvas, SCREEN_W / 2, CONTENT_MID_Y - 8,
                            AlignCenter, AlignCenter, "BLE Connected!");
    canvas_draw_str_aligned(canvas, SCREEN_W / 2, CONTENT_MID_Y + 2,
                            AlignCenter, AlignCenter,
                            history_count ? "Send text / Up:Log" : "Send text from client");

    draw_layout_line(canvas, layout);
}

static void draw_text_received(Canvas* canvas, const char* text, size_t offset, uint32_t delay) {
    draw_header(canvas);

    canvas_set_font(canvas, FontSecondary);
    char preview[64];
    size_t total = ttf_preview(text, offset, preview, sizeof(preview));
    char info[32];
    snprintf(info, sizeof(info), "%u/%u R:%lums U/D", (unsigned)(offset / 21 + 1),
        (unsigned)((total + 20) / 21), (unsigned long)delay);
    canvas_draw_str(canvas, 2, CONTENT_TOP + 7, info);

    // Afficher jusqu'à 3 lignes du texte (21 chars max par ligne)
    char line_buf[DISPLAY_COLS + 1];
    int y = CONTENT_TOP + 17;
    size_t start = 0;
    const int max_lines = 3;

    canvas_set_font(canvas, FontKeyboard);
    for(int line = 0; line < max_lines && start < strlen(preview); line++) {
        size_t count = strlen(preview) - start;
        if(count > DISPLAY_COLS) count = DISPLAY_COLS;
        memcpy(line_buf, preview + start, count);
        line_buf[count] = '\0';
        start += count;
        canvas_draw_str(canvas, 2, y, line_buf);
        y += 8;
    }

    draw_footer(canvas, "Back:Skip", "OK:Send");
}

static void draw_waiting_usb(Canvas* canvas, bool usb_detected) {
    draw_header(canvas);

    canvas_set_font(canvas, FontSecondary);
    if(!usb_detected) {
        canvas_draw_str_aligned(canvas, SCREEN_W / 2, CONTENT_MID_Y - 10,
                                AlignCenter, AlignCenter, "Plug Flipper into");
        canvas_draw_str_aligned(canvas, SCREEN_W / 2, CONTENT_MID_Y + 2,
                                AlignCenter, AlignCenter, "the target PC via USB");
        canvas_draw_str_aligned(canvas, SCREEN_W / 2, CONTENT_MID_Y + 14,
                                AlignCenter, AlignCenter, "Will send automatically");
    } else {
        canvas_draw_str_aligned(canvas, SCREEN_W / 2, CONTENT_MID_Y - 6,
                                AlignCenter, AlignCenter, "USB connected!");
        canvas_draw_str_aligned(canvas, SCREEN_W / 2, CONTENT_MID_Y + 8,
                                AlignCenter, AlignCenter, "Sending in a moment...");
    }

    draw_footer(canvas, "Back:Cancel", NULL);
}

static void draw_sending(Canvas* canvas, size_t progress, size_t total) {
    draw_header(canvas);

    canvas_set_font(canvas, FontSecondary);
    canvas_draw_str_aligned(canvas, SCREEN_W / 2, CONTENT_MID_Y - 4,
                            AlignCenter, AlignCenter, "Sending keystrokes...");
    char info[24];
    unsigned percent = total ? progress * 100 / total : 0;
    snprintf(info, sizeof(info), "%u%%", percent);
    canvas_draw_str(canvas, 52, 43, info);
    canvas_draw_frame(canvas, 4, 46, 120, 5);
    canvas_draw_box(canvas, 5, 47, percent * 118 / 100, 3);
    draw_footer(canvas, "Back:STOP", NULL);
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

/**
 * Liste de l'historique. `lines` contient la fenêtre visible (déjà extraite
 * sous mutex), `n` son nombre d'entrées, `sel_in_win` l'index sélectionné
 * dans cette fenêtre, `pos`/`total` la position 1-based pour l'indicateur.
 */
static void draw_history(
    Canvas* canvas,
    char lines[HIST_VISIBLE][HIST_LINE_LEN],
    size_t n,
    size_t sel_in_win,
    size_t pos,
    size_t total) {
    draw_header(canvas);
    canvas_set_font(canvas, FontSecondary);

    // Indicateur de position "n/total" en haut à droite
    char pos_buf[12];
    snprintf(pos_buf, sizeof(pos_buf), "%u/%u", (unsigned)pos, (unsigned)total);
    canvas_draw_str_aligned(canvas, SCREEN_W - 2, CONTENT_TOP + 5,
                            AlignRight, AlignCenter, pos_buf);

    // Heading at y=20; three rows at 30/40/50, clear of the footer at y=53.
    int y = CONTENT_TOP + 15;
    char buf[DISPLAY_COLS + 4];
    for(size_t i = 0; i < n; i++) {
        truncate_str(lines[i], buf, DISPLAY_COLS - 1);
        if(i == sel_in_win) {
            // Surbrillance : barre inversée
            canvas_draw_box(canvas, 0, y - 7, SCREEN_W, 9);
            canvas_set_color(canvas, ColorWhite);
            canvas_draw_str(canvas, 3, y, buf);
            canvas_set_color(canvas, ColorBlack);
        } else {
            canvas_draw_str(canvas, 3, y, buf);
        }
        y += 10;
    }

    draw_footer(canvas, "Back", "OK:Send");
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
    AppState state        = app->state;
    size_t   text_len     = app->text_len;
    bool bt_connected = app->bt_connected;
    bool usb_connected = app->usb_connected;
    size_t preview_offset = app->preview_offset;
    size_t progress = app->send_progress;
    uint32_t delay = app->key_delay_ms;
    bool     usb_detected = (app->usb_detect_tick != 0);
    size_t   hist_count   = app->history_count;
    char text_copy[TTF_TEXT_BUFFER_SIZE];
    char err_copy[TTF_ERROR_MSG_SIZE];
    char layout_copy[TTF_LAYOUT_NAME_SIZE];
    strncpy(text_copy, app->received_text, sizeof(text_copy) - 1);
    text_copy[sizeof(text_copy) - 1] = '\0';
    strncpy(err_copy, app->error_msg, sizeof(err_copy) - 1);
    err_copy[sizeof(err_copy) - 1] = '\0';
    strncpy(layout_copy, app->layout_name, sizeof(layout_copy) - 1);
    layout_copy[sizeof(layout_copy) - 1] = '\0';

    // Snapshot de la fenêtre d'historique visible (uniquement si pertinent)
    char   hist_win[HIST_VISIBLE][HIST_LINE_LEN];
    size_t hist_win_n     = 0;
    size_t hist_sel_inwin = 0;
    size_t hist_pos       = 0;
    if(state == AppStateHistory && hist_count > 0) {
        size_t sel = app->history_sel;
        if(sel >= hist_count) sel = hist_count - 1;
        size_t win_start = 0;
        if(sel >= HIST_VISIBLE) win_start = sel - (HIST_VISIBLE - 1);
        for(size_t i = 0; i < HIST_VISIBLE && (win_start + i) < hist_count; i++) {
            strncpy(hist_win[i], app->history[win_start + i], HIST_LINE_LEN - 1);
            hist_win[i][HIST_LINE_LEN - 1] = '\0';
            hist_win_n++;
        }
        hist_sel_inwin = sel - win_start;
        hist_pos       = sel + 1; // position 1-based
    }
    furi_mutex_release(app->mutex);

    canvas_clear(canvas);

    switch(state) {
    case AppStateWaitingBT:
        draw_waiting_bt(canvas, layout_copy, hist_count);
        break;
    case AppStateConnected:
        draw_connected(canvas, layout_copy, hist_count);
        break;
    case AppStateHistory:
        draw_history(canvas, hist_win, hist_win_n, hist_sel_inwin, hist_pos, hist_count);
        break;
    case AppStateTextReceived:
        draw_text_received(canvas, text_copy, preview_offset, delay);
        break;
    case AppStateWaitingUSB:
        draw_waiting_usb(canvas, usb_detected);
        break;
    case AppStateSending:
        draw_sending(canvas, progress, text_len);
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
    // Always show independent link states, including while previewing/sending.
    canvas_set_color(canvas, ColorWhite);
    canvas_draw_box(canvas, 0, 0, SCREEN_W, HEADER_H);
    canvas_set_color(canvas, ColorBlack);
    canvas_set_font(canvas, FontSecondary);
    char links[32];
    snprintf(links, sizeof(links), "BT:%s  USB:%s", bt_connected ? "ON" : "OFF",
        usb_connected ? "Ready" : "OFF");
    canvas_draw_str(canvas, 2, 10, links);
    if(state == AppStateConnected || state == AppStateWaitingBT) {
        char speed[24];
        snprintf(speed, sizeof(speed), "R:%lums", (unsigned long)delay);
        draw_footer(canvas, "L:Kbd", speed);
    }
}

void ttf_view_input_callback(InputEvent* event, void* context) {
    TransTheFlipApp* app = (TransTheFlipApp*)context;
    AppEvent ev;
    ev.type  = EventTypeInput;
    ev.input = *event;
    furi_message_queue_put(app->event_queue, &ev, FuriWaitForever);
}
