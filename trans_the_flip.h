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
#pragma once

#include <furi.h>
#include <furi_hal.h>
#include <gui/gui.h>
#include <input/input.h>

// ============================================================
// Constants
// ============================================================
#define TTF_TEXT_BUFFER_SIZE  256
#define TTF_ERROR_MSG_SIZE    64
#define TTF_EVENT_QUEUE_DEPTH 16
#define TTF_DONE_AUTO_MS      2000   // ms avant retour automatique à Connected
#define TTF_LAYOUT_NAME_SIZE  24     // longueur max du nom de layout affiché
#define TTF_LAYOUT_PATH_SIZE  128    // longueur max du chemin vers le fichier .kl
#define TTF_USB_CONNECT_DELAY_MS 1500 // délai après détection USB avant envoi

// ============================================================
// Machine d'états
// ============================================================
typedef enum {
    AppStateWaitingBT = 0,   // Attente connexion BLE
    AppStateConnected,        // Connecté, attente texte
    AppStateTextReceived,     // Texte reçu, confirmation en attente
    AppStateWaitingUSB,       // OK pressé mais USB HID non connecté — attente branchement
    AppStateSending,          // Envoi HID en cours
    AppStateDone,             // Terminé (retour auto)
    AppStateError,            // Erreur
} AppState;

// ============================================================
// Types d'événements
// ============================================================
typedef enum {
    EventTypeBtConnect = 0,
    EventTypeBtDisconnect,
    EventTypeBtData,
    EventTypeInput,
    EventTypeSendDone,
    EventTypeSendError,
} AppEventType;

typedef struct {
    AppEventType type;
    InputEvent   input;                   // valide si type == EventTypeInput
    char         text[TTF_TEXT_BUFFER_SIZE]; // valide si type == EventTypeBtData
    size_t       text_len;
} AppEvent;

// ============================================================
// Structure principale de l'application
// ============================================================
typedef struct {
    // GUI
    Gui*      gui;
    ViewPort* view_port;

    // Synchronisation inter-threads
    FuriMessageQueue* event_queue;
    FuriMutex*        mutex;

    // Thread d'envoi HID (lancé lors de AppStateSending)
    FuriThread* send_thread;

    // État courant
    AppState state;

    // Buffer texte reçu (accumulé depuis plusieurs paquets BLE)
    char   received_text[TTF_TEXT_BUFFER_SIZE];
    size_t text_len;

    // Message d'erreur
    char error_msg[TTF_ERROR_MSG_SIZE];

    // Timestamp (tick) pour le retour auto depuis AppStateDone
    uint32_t done_tick;

    // Layout clavier actif
    char layout_name[TTF_LAYOUT_NAME_SIZE]; // ex. "fr-FR" (affiché à l'écran)
    char layout_path[TTF_LAYOUT_PATH_SIZE]; // chemin complet vers le .kl

    // Tick de détection USB (état WaitingUSB) — 0 = USB pas encore vu
    uint32_t usb_detect_tick;
} TransTheFlipApp;
