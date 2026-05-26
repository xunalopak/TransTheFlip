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

// ============================================================
// Machine d'états
// ============================================================
typedef enum {
    AppStateWaitingBT = 0,   // Attente connexion BLE
    AppStateConnected,        // Connecté, attente texte
    AppStateTextReceived,     // Texte reçu, confirmation en attente
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
} TransTheFlipApp;
