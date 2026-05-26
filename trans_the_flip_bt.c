#include "trans_the_flip_bt.h"
#include "trans_the_flip.h"

#include <furi.h>
#include <bt/bt_service/bt.h>
#include <profiles/serial_profile.h>
#include <string.h>

// ============================================================
// État module (singletons — une seule instance de l'app)
// ============================================================
static Bt*                   s_bt      = NULL;
static FuriHalBleProfileBase* s_profile = NULL;
static TransTheFlipApp*      s_app     = NULL;

// ============================================================
// Callback : changement de statut BLE (connect / disconnect)
// Appelé depuis le thread BT service.
// ============================================================
static void bt_status_callback(BtStatus status, void* context) {
    TransTheFlipApp* app = (TransTheFlipApp*)context;
    AppEvent ev;
    memset(&ev, 0, sizeof(ev));

    switch(status) {
    case BtStatusConnected:
        ev.type = EventTypeBtConnect;
        furi_message_queue_put(app->event_queue, &ev, 0);
        break;

    case BtStatusAdvertising:
    case BtStatusOff:
    case BtStatusUnavailable:
        ev.type = EventTypeBtDisconnect;
        furi_message_queue_put(app->event_queue, &ev, 0);
        break;

    default:
        break;
    }
}

// ============================================================
// Callback : données reçues via BLE serial
// SerialServiceEventCallback = uint16_t (*)(SerialServiceEvent, void*)
// Retourne le nombre d'octets consommés.
// ============================================================
static uint16_t serial_data_callback(SerialServiceEvent event, void* context) {
    TransTheFlipApp* app = (TransTheFlipApp*)context;

    if(event.event == SerialServiceEventTypeDataReceived && event.data.size > 0) {
        AppEvent ev;
        memset(&ev, 0, sizeof(ev));
        ev.type = EventTypeBtData;

        // Copier les données reçues dans l'événement (tronquer si trop grand)
        uint16_t copy_size = event.data.size;
        if(copy_size >= TTF_TEXT_BUFFER_SIZE) {
            copy_size = TTF_TEXT_BUFFER_SIZE - 1;
        }
        memcpy(ev.text, event.data.buffer, copy_size);
        ev.text[copy_size] = '\0';
        ev.text_len = copy_size;

        furi_message_queue_put(app->event_queue, &ev, 0);

        // NE PAS appeler notify_buffer_is_empty ici !
        // Le firmware tient buff_size_mtx pendant tout l'appel du callback.
        // Appeler notify_buffer_is_empty ici → deadlock immédiat.
        // On le fait depuis le thread principal (EventTypeBtData handler).
    }

    return event.data.size; // octets consommés
}

// ============================================================
// API publique
// ============================================================

bool ttf_bt_init(TransTheFlipApp* app) {
    s_app = app;
    s_bt = furi_record_open(RECORD_BT);
    if(!s_bt) return false;

    // Enregistrer le callback de statut AVANT de démarrer le profil
    bt_set_status_changed_callback(s_bt, bt_status_callback, app);

    // Basculer vers le profil serial BLE (redémarre le cœur BLE)
    s_profile = bt_profile_start(s_bt, ble_profile_serial, NULL);
    if(!s_profile) {
        bt_set_status_changed_callback(s_bt, NULL, NULL);
        furi_record_close(RECORD_BT);
        s_bt = NULL;
        return false;
    }

    // Enregistrer le callback de réception de données
    ble_profile_serial_set_event_callback(
        s_profile,
        BLE_PROFILE_SERIAL_PACKET_SIZE_MAX,
        serial_data_callback,
        app);

    // Désactiver le mode RPC (qFlipper/CLI) pour que les données arrivent
    // dans notre callback. Par défaut le profil serial est en mode RPC et
    // toutes les données écrites sur la caractéristique RX sont interceptées
    // par le handler qFlipper avant d'atteindre SerialServiceEventCallback.
    ble_profile_serial_set_rpc_active(s_profile, false);

    // Amorcer le flow control : signaler dès l'init que notre buffer est vide
    // et prêt à recevoir. Sans cet appel initial, le service retient les données
    // jusqu'à ce qu'on envoie ce signal — aucune donnée n'arrive jamais.
    ble_profile_serial_notify_buffer_is_empty(s_profile);

    return true;
}

void ttf_bt_deinit(TransTheFlipApp* app) {
    UNUSED(app);

    if(s_profile) {
        // Désenregistrer le callback de données
        ble_profile_serial_set_event_callback(s_profile, 0, NULL, NULL);
        s_profile = NULL;
    }

    if(s_bt) {
        // Désenregistrer le callback de statut
        bt_set_status_changed_callback(s_bt, NULL, NULL);
        // Restaurer le profil par défaut (redémarre le cœur BLE)
        bt_profile_restore_default(s_bt);
        furi_record_close(RECORD_BT);
        s_bt = NULL;
    }
}

void ttf_bt_notify_ready(void) {
    if(!s_profile) return;
    ble_profile_serial_notify_buffer_is_empty(s_profile);
}

void ttf_bt_on_connect(void) {
    if(!s_profile) return;

    // CRUCIAL : à chaque connexion, le service `bt` du firmware ré-écrase NOTRE
    // callback de données par le sien (bt_serial_event_callback, qui route vers
    // RPC) et réactive le mode RPC — cf. bt.c, handler GapEventTypeConnected.
    // Conséquence : après connexion, toutes les données RX partent vers RPC et
    // n'atteignent jamais serial_data_callback (compteur RX reste à 0).
    //
    // On reprend la main en ré-enregistrant notre propre callback ICI, une fois
    // la connexion établie. L'ordre est garanti : le service bt pose son callback
    // dans le handler GAP *avant* d'émettre le statut "Connected" qui déclenche
    // cet appel. Ce ré-enregistrement repositionne aussi bytes_ready_to_receive
    // = buff_size (cf. ble_svc_serial_set_callbacks) → réarme le flow control.
    ble_profile_serial_set_event_callback(
        s_profile,
        BLE_PROFILE_SERIAL_PACKET_SIZE_MAX,
        serial_data_callback,
        s_app);

    // Désactiver le mode RPC (met à jour la caractéristique de statut côté PC).
    ble_profile_serial_set_rpc_active(s_profile, false);

    // Réamorcer le flow control : signaler que notre buffer est prêt.
    ble_profile_serial_notify_buffer_is_empty(s_profile);
}

void ttf_bt_send_status(const char* msg) {
    if(!s_profile || !msg) return;
    size_t len = strlen(msg);
    if(len == 0 || len > 512) return;
    ble_profile_serial_tx(s_profile, (uint8_t*)msg, (uint16_t)len);
}
