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
#include "trans_the_flip.h"
#include "trans_the_flip_hid.h"
#include "trans_the_flip_bt.h"
#include "trans_the_flip_view.h"

#include <furi.h>
#include <furi_hal.h>
#include <gui/gui.h>
#include <dialogs/dialogs.h>
#include <storage/storage.h>
#include <string.h>
#include <stdlib.h>
#include <stdio.h>

// ============================================================
// Chemins pour le layout clavier
// ============================================================
#define TTF_LAYOUT_FOLDER  "/ext/badusb/assets/layouts"
#define TTF_SETTINGS_DIR   "/ext/apps_data/trans_the_flip"
#define TTF_SETTINGS_PATH  TTF_SETTINGS_DIR "/layout.conf"

// ============================================================
// Thread d'envoi HID (séparé pour ne pas bloquer l'UI)
// ============================================================
typedef struct {
    TransTheFlipApp* app;
    char             text[TTF_TEXT_BUFFER_SIZE];
    size_t           text_len;
} SendThreadCtx;

static int32_t send_thread_fn(void* raw_ctx) {
    SendThreadCtx* ctx = (SendThreadCtx*)raw_ctx;
    TransTheFlipApp* app = ctx->app;

    bool ok = ttf_hid_send_string(ctx->text, ctx->text_len);

    AppEvent ev;
    ev.type = ttf_hid_cancelled() ? EventTypeSendCancelled :
        (ok ? EventTypeSendDone : EventTypeSendError);
    furi_message_queue_put(app->event_queue, &ev, FuriWaitForever);

    free(ctx);
    return 0;
}

// ============================================================
// Allocation / libération de l'app
// ============================================================
static TransTheFlipApp* app_alloc(void) {
    TransTheFlipApp* app = malloc(sizeof(TransTheFlipApp));
    if(!app) return NULL;

    memset(app, 0, sizeof(TransTheFlipApp));
    app->state = AppStateWaitingBT;
    app->key_delay_ms = 8;
    atomic_init(&app->rx_overflow, false);

    // Queue d'événements
    app->event_queue = furi_message_queue_alloc(TTF_EVENT_QUEUE_DEPTH, sizeof(AppEvent));

    // Mutex pour protection de l'état partagé avec le thread GUI
    app->mutex = furi_mutex_alloc(FuriMutexTypeNormal);

    // GUI
    app->view_port = view_port_alloc();
    view_port_draw_callback_set(app->view_port, ttf_view_draw_callback, app);
    view_port_input_callback_set(app->view_port, ttf_view_input_callback, app);

    app->gui = furi_record_open(RECORD_GUI);
    gui_add_view_port(app->gui, app->view_port, GuiLayerFullscreen);

    return app;
}

static void app_free(TransTheFlipApp* app) {
    if(!app) return;

    // Attendre la fin du thread d'envoi s'il est encore actif
    if(app->send_thread) {
        furi_thread_join(app->send_thread);
        furi_thread_free(app->send_thread);
        app->send_thread = NULL;
    }

    gui_remove_view_port(app->gui, app->view_port);
    furi_record_close(RECORD_GUI);
    view_port_free(app->view_port);

    furi_mutex_free(app->mutex);
    furi_message_queue_free(app->event_queue);

    free(app);
}

// ============================================================
// Déclenchement de l'envoi dans un thread séparé
// ============================================================
static void start_send(TransTheFlipApp* app) {
    if(app->send_thread) return;
    ttf_hid_prepare(app->key_delay_ms);
    app->send_progress = 0;
    // Allouer le contexte du thread (il sera libéré dans send_thread_fn)
    SendThreadCtx* ctx = malloc(sizeof(SendThreadCtx));
    if(!ctx) {
        furi_mutex_acquire(app->mutex, FuriWaitForever);
        strncpy(app->error_msg, "Out of memory", TTF_ERROR_MSG_SIZE - 1);
        app->state = AppStateError;
        furi_mutex_release(app->mutex);
        view_port_update(app->view_port);
        ttf_bt_send_status("ERR:MEMORY\n");
        return;
    }

    ctx->app = app;
    strncpy(ctx->text, app->received_text, TTF_TEXT_BUFFER_SIZE - 1);
    ctx->text[TTF_TEXT_BUFFER_SIZE - 1] = '\0';
    ctx->text_len = app->text_len;

    app->send_thread = furi_thread_alloc();
    furi_thread_set_name(app->send_thread, "TTF_Send");
    furi_thread_set_stack_size(app->send_thread, 2048);
    furi_thread_set_context(app->send_thread, ctx);
    furi_thread_set_callback(app->send_thread, send_thread_fn);
    furi_thread_start(app->send_thread);
    ttf_bt_send_status("SENDING\n");
}

// ============================================================
// Gestion du layout clavier
// ============================================================

/** Extrait le nom de fichier sans extension depuis un chemin complet. */
static void layout_name_from_path(const char* path, char* out, size_t out_size) {
    if(!path || !path[0]) {
        strncpy(out, "QWERTY US", out_size - 1);
        out[out_size - 1] = '\0';
        return;
    }
    // Trouver le dernier '/'
    const char* slash = strrchr(path, '/');
    const char* base = slash ? slash + 1 : path;
    // Copier sans l'extension .kl
    strncpy(out, base, out_size - 1);
    out[out_size - 1] = '\0';
    char* dot = strrchr(out, '.');
    if(dot) *dot = '\0';
}

/** Sauvegarde le chemin du layout dans le fichier de config. */
static void save_layout_setting(const char* path) {
    Storage* storage = furi_record_open(RECORD_STORAGE);
    // Créer le répertoire si nécessaire
    storage_simply_mkdir(storage, TTF_SETTINGS_DIR);
    File* file = storage_file_alloc(storage);
    if(storage_file_open(file, TTF_SETTINGS_PATH, FSAM_WRITE, FSOM_CREATE_ALWAYS)) {
        storage_file_write(file, path, strlen(path));
        storage_file_close(file);
    }
    storage_file_free(file);
    furi_record_close(RECORD_STORAGE);
}

/** Charge le layout sauvegardé au démarrage. Fallback : QWERTY US intégré. */
static void load_layout_setting(TransTheFlipApp* app) {
    Storage* storage = furi_record_open(RECORD_STORAGE);
    File* file = storage_file_alloc(storage);
    bool loaded = false;

    if(storage_file_open(file, TTF_SETTINGS_PATH, FSAM_READ, FSOM_OPEN_EXISTING)) {
        char path_buf[TTF_LAYOUT_PATH_SIZE];
        memset(path_buf, 0, sizeof(path_buf));
        uint16_t r = storage_file_read(file, path_buf, sizeof(path_buf) - 1);
        storage_file_close(file);
        if(r > 0 && path_buf[0] != '\0') {
            if(ttf_hid_load_layout(path_buf)) {
                strncpy(app->layout_path, path_buf, TTF_LAYOUT_PATH_SIZE - 1);
                layout_name_from_path(path_buf, app->layout_name, TTF_LAYOUT_NAME_SIZE);
                loaded = true;
            }
        }
    }

    storage_file_free(file);
    furi_record_close(RECORD_STORAGE);

    if(!loaded) {
        ttf_hid_reset_layout();
        strncpy(app->layout_name, "QWERTY US", TTF_LAYOUT_NAME_SIZE - 1);
        app->layout_path[0] = '\0';
    }
}

/** Ouvre le sélecteur de layout et applique le choix. Appelé hors mutex. */
static void open_layout_picker(TransTheFlipApp* app) {
    DialogsApp* dialogs = furi_record_open(RECORD_DIALOGS);

    DialogsFileBrowserOptions opts;
    dialog_file_browser_set_basic_options(&opts, ".kl", NULL);

    FuriString* path = furi_string_alloc_set(TTF_LAYOUT_FOLDER);
    bool selected = dialog_file_browser_show(dialogs, path, path, &opts);

    furi_record_close(RECORD_DIALOGS);

    if(selected && furi_string_size(path) > 0) {
        const char* path_cstr = furi_string_get_cstr(path);
        if(ttf_hid_load_layout(path_cstr)) {
            furi_mutex_acquire(app->mutex, FuriWaitForever);
            strncpy(app->layout_path, path_cstr, TTF_LAYOUT_PATH_SIZE - 1);
            layout_name_from_path(path_cstr, app->layout_name, TTF_LAYOUT_NAME_SIZE);
            furi_mutex_release(app->mutex);
            save_layout_setting(path_cstr);
        }
    }

    furi_string_free(path);
    view_port_update(app->view_port);
}

// ============================================================
// Réinitialise le buffer de texte (après envoi ou annulation)
// Appel sous mutex.
// ============================================================
static void reset_text_buffer(TransTheFlipApp* app) {
    memset(app->received_text, 0, TTF_TEXT_BUFFER_SIZE);
    app->text_len = 0;
    app->preview_offset = 0;
    ttf_rx_reset(&app->receiver);
    app->rx_tick = 0;
}

static AppState idle_state(TransTheFlipApp* app) {
    return app->bt_connected ? AppStateConnected : AppStateWaitingBT;
}

static void receive_error(TransTheFlipApp* app, const char* message, const char* status) {
    ttf_rx_reset(&app->receiver);
    app->rx_tick = 0;
    if(!app->send_thread) {
        reset_text_buffer(app);
        app->state = AppStateError;
        snprintf(app->error_msg, sizeof(app->error_msg), "%s", message);
    }
    ttf_bt_send_status(status);
}

// ============================================================
// Historique des textes envoyés (RAM uniquement).
// La plus récente est en index 0. Effacé à la fermeture de l'app
// (jamais persisté sur la carte SD). Appel sous mutex.
// ============================================================
static void history_push(TransTheFlipApp* app, const char* text, size_t len) {
    if(len == 0 || !text || !text[0]) return;

    // Éviter un doublon consécutif (même texte que la dernière entrée)
    if(app->history_count > 0 &&
       strncmp(app->history[0], text, TTF_TEXT_BUFFER_SIZE) == 0) {
        return;
    }

    // Décaler les entrées existantes vers le bas pour libérer l'index 0
    int last = (app->history_count < TTF_HISTORY_MAX) ? (int)app->history_count
                                                      : TTF_HISTORY_MAX - 1;
    for(int i = last; i > 0; i--) {
        memcpy(app->history[i], app->history[i - 1], TTF_TEXT_BUFFER_SIZE);
    }

    strncpy(app->history[0], text, TTF_TEXT_BUFFER_SIZE - 1);
    app->history[0][TTF_TEXT_BUFFER_SIZE - 1] = '\0';

    if(app->history_count < TTF_HISTORY_MAX) {
        app->history_count++;
    }
}

// ============================================================
// Point d'entrée de l'application
// ============================================================
int32_t trans_the_flip_app(void* p) {
    UNUSED(p);

    // --- Allocation ---
    TransTheFlipApp* app = app_alloc();
    if(!app) return -1;

    // --- Init USB HID ---
    if(!ttf_hid_init()) {
        furi_mutex_acquire(app->mutex, FuriWaitForever);
        strncpy(app->error_msg, "USB HID init failed", TTF_ERROR_MSG_SIZE - 1);
        app->state = AppStateError;
        furi_mutex_release(app->mutex);
        view_port_update(app->view_port);
    }

    // --- Chargement du layout (après HID init) ---
    load_layout_setting(app);

    // --- Init BLE serial ---
    ttf_bt_init(app);

    // Rafraîchissement initial
    view_port_update(app->view_port);

    // ============================================================
    // Boucle principale d'événements
    // ============================================================
    bool running = true;
    AppEvent ev;

    while(running) {
        // Attente d'un événement (timeout 100 ms pour les transitions automatiques)
        FuriStatus status = furi_message_queue_get(app->event_queue, &ev, 100);

        if(status == FuriStatusOk) {
            furi_mutex_acquire(app->mutex, FuriWaitForever);

            switch(ev.type) {

            // --------------------------------------------------
            case EventTypeBtConnect:
                app->bt_connected = true;
                if(app->state == AppStateWaitingBT || app->state == AppStateDone) {
                    app->state = AppStateConnected;
                    app->done_tick = 0;
                }
                // Réappliquer RPC-off + flow control à chaque connexion
                // (le firmware Momentum peut les réinitialiser sur reconnexion)
                furi_mutex_release(app->mutex);
                ttf_bt_on_connect();
                furi_mutex_acquire(app->mutex, FuriWaitForever);
                break;

            // --------------------------------------------------
            case EventTypeBtDisconnect:
                app->bt_connected = false;
                ttf_hid_cancel();
                if(!app->send_thread) {
                    app->state = AppStateWaitingBT;
                    reset_text_buffer(app);
                }
                break;

            // --------------------------------------------------
            case EventTypeBtData:
                app->bt_connected = true;
                // Si on reçoit des données alors qu'on est encore en WaitingBT,
                // c'est que la connexion BLE est établie mais le callback de
                // statut n'a pas déclenché → on auto-transition vers Connected.
                if(app->state == AppStateWaitingBT || app->state == AppStateDone) {
                    app->state = AppStateConnected;
                    app->done_tick = 0;
                }
                // Signaler au serial service que notre buffer est libéré.
                // DOIT être fait depuis ce thread (pas depuis serial_data_callback
                // qui est appelé avec buff_size_mtx déjà tenu → deadlock).
                furi_mutex_release(app->mutex);
                ttf_bt_notify_ready();
                furi_mutex_acquire(app->mutex, FuriWaitForever);
                if(app->state != AppStateConnected) {
                    ttf_bt_send_status("ERR:BUSY\n");
                    break;
                }
                for(size_t i = 0; i < ev.text_len; i++) {
                    app->rx_tick = furi_get_tick();
                    TtfRxResult result = ttf_rx_feed(&app->receiver, (uint8_t)ev.text[i]);
                    if(result == TtfRxHello) {
                        app->rx_tick = 0;
                        ttf_bt_send_status("READY:1:255\n");
                    } else if(result == TtfRxComplete) {
                        if(i + 1 != ev.text_len) {
                            receive_error(app, "Extra data rejected", "ERR:PROTOCOL\n");
                            break;
                        }
                        app->text_len = app->receiver.length;
                        memcpy(app->received_text, app->receiver.text, app->text_len + 1);
                        app->preview_offset = 0;
                        app->state = AppStateTextReceived;
                        app->rx_tick = 0;
                        ttf_rx_reset(&app->receiver);
                        ttf_bt_send_status("RECV\n");
                    } else if(result != TtfRxMore) {
                        receive_error(app,
                            result == TtfRxTooLong ? "Text too long" :
                            result == TtfRxChecksum ? "Incomplete/corrupt text" :
                            result == TtfRxUnsupported ? "Unsupported character" : "Invalid protocol",
                            result == TtfRxTooLong ? "ERR:LENGTH\n" :
                            result == TtfRxChecksum ? "ERR:CHECKSUM\n" :
                            result == TtfRxUnsupported ? "ERR:CHAR\n" : "ERR:PROTOCOL\n");
                        break;
                    }
                }
                break;

            // --------------------------------------------------
            case EventTypeInput:
                if(ev.input.key == InputKeyBack && ev.input.type == InputTypePress &&
                   app->state == AppStateSending) {
                    ttf_hid_cancel();
                    app->ignore_back_release = true;
                }
                if(ev.input.key == InputKeyBack && app->ignore_back_release &&
                   (ev.input.type == InputTypeShort || ev.input.type == InputTypeLong)) {
                    app->ignore_back_release = false;
                    break;
                }
                if(ev.input.type == InputTypeShort || ev.input.type == InputTypeLong) {
                    switch(ev.input.key) {

                    case InputKeyOk:
                        if(app->state == AppStateHistory) {
                            // Recharger l'entrée sélectionnée → écran de confirmation
                            if(app->history_sel < app->history_count) {
                                strncpy(app->received_text,
                                        app->history[app->history_sel],
                                        TTF_TEXT_BUFFER_SIZE - 1);
                                app->received_text[TTF_TEXT_BUFFER_SIZE - 1] = '\0';
                                app->text_len = strlen(app->received_text);
                                app->preview_offset = 0;
                                app->state    = AppStateTextReceived;
                            }
                        } else if(app->state == AppStateTextReceived) {
                            // Vérifier que le Flipper est bien branché en USB HID
                            // à un PC avant de lancer l'envoi
                            if(furi_hal_hid_is_connected()) {
                                app->state = AppStateSending;
                                furi_mutex_release(app->mutex);
                                start_send(app);
                                furi_mutex_acquire(app->mutex, FuriWaitForever);
                            } else {
                                // USB non connecté : attendre le branchement
                                app->state           = AppStateWaitingUSB;
                                app->usb_detect_tick = 0;
                                ttf_bt_send_status("WAIT_USB\n");
                            }
                        } else if(app->state == AppStateError) {
                            // OK depuis l'écran d'erreur → retour
                            reset_text_buffer(app);
                            app->state = idle_state(app);
                            strncpy(app->error_msg, "", 1);
                        }
                        break;

                    case InputKeyUp:
                        if(app->state == AppStateTextReceived) {
                            if(app->preview_offset >= 21) app->preview_offset -= 21;
                        } else if(app->state == AppStateHistory) {
                            // Naviguer vers l'entrée plus récente
                            if(app->history_sel > 0) app->history_sel--;
                        } else if(app->state == AppStateWaitingBT ||
                                  app->state == AppStateConnected) {
                            // Ouvrir l'historique (s'il contient au moins une entrée)
                            if(app->history_count > 0) {
                                app->history_return_state = app->state;
                                app->history_sel          = 0;
                                app->state                = AppStateHistory;
                            }
                        }
                        break;

                    case InputKeyDown:
                        if(app->state == AppStateTextReceived) {
                            if(app->preview_offset + 63 < ttf_preview(app->received_text, 0, NULL, 0))
                                app->preview_offset += 21;
                        } else if(app->state == AppStateHistory) {
                            // Naviguer vers l'entrée plus ancienne
                            if(app->history_sel + 1 < app->history_count) {
                                app->history_sel++;
                            }
                        }
                        break;

                    case InputKeyRight:
                        if(app->state == AppStateWaitingBT || app->state == AppStateConnected ||
                           app->state == AppStateTextReceived) {
                            const uint32_t speeds[] = {8, 25, 50, 100, 250};
                            size_t index = 0;
                            while(index < 5 && speeds[index] != app->key_delay_ms) index++;
                            app->key_delay_ms = speeds[(index + 1) % 5];
                        }
                        break;

                    case InputKeyLeft:
                        // Ouvre le sélecteur de layout depuis WaitingBT ou Connected
                        if(app->state == AppStateWaitingBT || app->state == AppStateConnected) {
                            furi_mutex_release(app->mutex);
                            open_layout_picker(app);
                            furi_mutex_acquire(app->mutex, FuriWaitForever);
                        }
                        break;

                    case InputKeyBack:
                        switch(app->state) {
                        case AppStateTextReceived:
                            // Annuler → retour à Connected
                            app->state = idle_state(app);
                            reset_text_buffer(app);
                            ttf_bt_send_status("CANCEL\n");
                            break;
                        case AppStateWaitingUSB:
                            // Annuler l'attente USB → retour à Connected
                            app->state           = idle_state(app);
                            app->usb_detect_tick = 0;
                            reset_text_buffer(app);
                            ttf_bt_send_status("CANCEL\n");
                            break;
                        case AppStateSending:
                            ttf_hid_cancel();
                            break;
                        case AppStateError:
                            reset_text_buffer(app);
                            app->state = idle_state(app);
                            break;
                        case AppStateDone:
                            // Retour anticipé
                            app->state = idle_state(app);
                            reset_text_buffer(app);
                            app->done_tick = 0;
                            break;
                        case AppStateHistory:
                            // Quitter l'historique → revenir à l'état précédent
                            app->state = app->history_return_state;
                            break;
                        case AppStateWaitingBT:
                        case AppStateConnected:
                        default:
                            // Quitter l'application
                            running = false;
                            break;
                        }
                        break;

                    default:
                        break;
                    }
                }
                break;

            // --------------------------------------------------
            case EventTypeSendDone:
                // Libération du thread
                if(app->send_thread) {
                    furi_thread_join(app->send_thread);
                    furi_thread_free(app->send_thread);
                    app->send_thread = NULL;
                }
                app->state    = AppStateDone;
                app->done_tick = furi_get_tick();
                // Enregistrer le texte envoyé dans l'historique (avant reset)
                history_push(app, app->received_text, app->text_len);
                reset_text_buffer(app);
                ttf_bt_send_status("OK\n");
                break;

            // --------------------------------------------------
            case EventTypeSendError:
            case EventTypeSendCancelled:
                if(app->send_thread) {
                    furi_thread_join(app->send_thread);
                    furi_thread_free(app->send_thread);
                    app->send_thread = NULL;
                }
                app->state = ev.type == EventTypeSendCancelled ? idle_state(app) : AppStateError;
                strncpy(app->error_msg, "USB lost / HID failed", TTF_ERROR_MSG_SIZE - 1);
                reset_text_buffer(app);
                ttf_bt_send_status(ev.type == EventTypeSendCancelled ? "CANCEL\n" : "ERR:HID\n");
                break;

            default:
                break;
            }

            furi_mutex_release(app->mutex);
            view_port_update(app->view_port);

        }
        {
            // Timeout 100 ms : transitions automatiques
            furi_mutex_acquire(app->mutex, FuriWaitForever);
            bool need_update = false;
            bool usb = furi_hal_hid_is_connected();
            if(usb != app->usb_connected) {
                app->usb_connected = usb;
                need_update = true;
            }
            if(atomic_exchange(&app->rx_overflow, false)) {
                receive_error(app, "BLE data lost", "ERR:OVERFLOW\n");
                furi_mutex_release(app->mutex);
                ttf_bt_notify_ready();
                furi_mutex_acquire(app->mutex, FuriWaitForever);
                need_update = true;
            }
            if((app->receiver.header_len || app->receiver.expected) &&
               furi_get_tick() - app->rx_tick >= TTF_RX_TIMEOUT_MS) {
                receive_error(app, "Transfer incomplete", "ERR:TIMEOUT\n");
                need_update = true;
            }
            if(app->state == AppStateSending) {
                size_t progress = ttf_hid_progress();
                if(progress != app->send_progress) {
                    app->send_progress = progress;
                    char message[32];
                    snprintf(message, sizeof(message), "PROGRESS:%u\n",
                        (unsigned)(app->text_len ? progress * 100 / app->text_len : 0));
                    ttf_bt_send_status(message);
                    need_update = true;
                }
            }

            // Retour auto depuis AppStateDone
            if(app->state == AppStateDone && app->done_tick != 0) {
                if(furi_get_tick() - app->done_tick > TTF_DONE_AUTO_MS) {
                    app->state    = idle_state(app);
                    app->done_tick = 0;
                    need_update   = true;
                }
            }

            // Gestion du délai post-connexion USB avant envoi
            if(app->state == AppStateWaitingUSB) {
                if(furi_hal_hid_is_connected()) {
                    if(app->usb_detect_tick == 0) {
                        // Première détection : démarrer le compte-à-rebours
                        app->usb_detect_tick = furi_get_tick();
                        need_update = true; // rafraîchir l'écran (USB detected)
                    } else if(furi_get_tick() - app->usb_detect_tick >= TTF_USB_CONNECT_DELAY_MS) {
                        // Délai écoulé → lancer l'envoi
                        app->state = AppStateSending;
                        furi_mutex_release(app->mutex);
                        start_send(app);
                        furi_mutex_acquire(app->mutex, FuriWaitForever);
                        need_update = true;
                    }
                } else {
                    // USB débranché pendant l'attente → remettre le tick à zéro
                    if(app->usb_detect_tick != 0) {
                        app->usb_detect_tick = 0;
                        need_update = true;
                    }
                }
            }

            furi_mutex_release(app->mutex);
            if(need_update) {
                view_port_update(app->view_port);
            }
        }
    }

    // ============================================================
    // Cleanup
    // ============================================================
    ttf_bt_deinit(app);
    ttf_hid_deinit();
    app_free(app);

    return 0;
}
