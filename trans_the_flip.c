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
    ev.type = ok ? EventTypeSendDone : EventTypeSendError;
    furi_message_queue_put(app->event_queue, &ev, 0);

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
    // Allouer le contexte du thread (il sera libéré dans send_thread_fn)
    SendThreadCtx* ctx = malloc(sizeof(SendThreadCtx));
    if(!ctx) {
        furi_mutex_acquire(app->mutex, FuriWaitForever);
        strncpy(app->error_msg, "Out of memory", TTF_ERROR_MSG_SIZE - 1);
        app->state = AppStateError;
        furi_mutex_release(app->mutex);
        view_port_update(app->view_port);
        return;
    }

    ctx->app = app;
    strncpy(ctx->text, app->received_text, TTF_TEXT_BUFFER_SIZE - 1);
    ctx->text[TTF_TEXT_BUFFER_SIZE - 1] = '\0';
    ctx->text_len = app->text_len;

    app->send_thread = furi_thread_alloc();
    furi_thread_set_name(app->send_thread, "TTF_Send");
    furi_thread_set_stack_size(app->send_thread, 1024);
    furi_thread_set_context(app->send_thread, ctx);
    furi_thread_set_callback(app->send_thread, send_thread_fn);
    furi_thread_start(app->send_thread);
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
                if(app->state == AppStateWaitingBT) {
                    app->state = AppStateConnected;
                }
                // Réappliquer RPC-off + flow control à chaque connexion
                // (le firmware Momentum peut les réinitialiser sur reconnexion)
                furi_mutex_release(app->mutex);
                ttf_bt_on_connect();
                furi_mutex_acquire(app->mutex, FuriWaitForever);
                break;

            // --------------------------------------------------
            case EventTypeBtDisconnect:
                // Retour à l'écran d'attente quelle que soit l'étape
                if(app->state != AppStateError) {
                    app->state = AppStateWaitingBT;
                }
                // Annuler un éventuel envoi en cours (le thread finira tout seul)
                reset_text_buffer(app);
                break;

            // --------------------------------------------------
            case EventTypeBtData:
                // Si on reçoit des données alors qu'on est encore en WaitingBT,
                // c'est que la connexion BLE est établie mais le callback de
                // statut n'a pas déclenché → on auto-transition vers Connected.
                if(app->state == AppStateWaitingBT) {
                    app->state = AppStateConnected;
                }
                // Signaler au serial service que notre buffer est libéré.
                // DOIT être fait depuis ce thread (pas depuis serial_data_callback
                // qui est appelé avec buff_size_mtx déjà tenu → deadlock).
                furi_mutex_release(app->mutex);
                ttf_bt_notify_ready();
                furi_mutex_acquire(app->mutex, FuriWaitForever);
                // Accumulation des chunks BLE jusqu'à '\n'
                if(app->state == AppStateConnected) {
                    for(size_t i = 0; i < ev.text_len; i++) {
                        char c = ev.text[i];

                        if(c == '\n' || c == '\0') {
                            // Terminateur → texte complet reçu
                            app->received_text[app->text_len] = '\0';
                            if(app->text_len > 0) {
                                app->state = AppStateTextReceived;
                                // Feedback immédiat au PC
                                ttf_bt_send_status("RECV\n");
                            }
                            // Ne pas appeler reset_text_buffer ici :
                            // on garde le texte pour affichage et envoi.
                            break;
                        } else if(c != '\r') {
                            if(app->text_len < TTF_TEXT_BUFFER_SIZE - 1) {
                                app->received_text[app->text_len++] = c;
                            }
                        }
                    }
                }
                break;

            // --------------------------------------------------
            case EventTypeInput:
                if(ev.input.type == InputTypeShort || ev.input.type == InputTypeLong) {
                    switch(ev.input.key) {

                    case InputKeyOk:
                        if(app->state == AppStateTextReceived) {
                            // Vérifier que le Flipper est bien branché en USB HID
                            // à un PC avant de lancer l'envoi
                            if(furi_hal_hid_is_connected()) {
                                app->state = AppStateSending;
                                furi_mutex_release(app->mutex);
                                start_send(app);
                                furi_mutex_acquire(app->mutex, FuriWaitForever);
                            } else {
                                // USB non connecté : attendre le branchement
                                app->state = AppStateWaitingUSB;
                            }
                        } else if(app->state == AppStateError) {
                            // OK depuis l'écran d'erreur → retour
                            app->state = AppStateWaitingBT;
                            strncpy(app->error_msg, "", 1);
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
                            app->state = AppStateConnected;
                            reset_text_buffer(app);
                            ttf_bt_send_status("CANCEL\n");
                            break;
                        case AppStateWaitingUSB:
                            // Annuler l'attente USB → retour à Connected
                            app->state = AppStateConnected;
                            reset_text_buffer(app);
                            ttf_bt_send_status("CANCEL\n");
                            break;
                        case AppStateSending:
                            // Envoi en cours : ne pas interrompre le thread HID
                            // (interrompre en plein milieu laisserait des touches enfoncées)
                            // On ignore le Back — l'écran affiche "Do not unplug USB"
                            break;
                        case AppStateError:
                            app->state = AppStateWaitingBT;
                            break;
                        case AppStateDone:
                            // Retour anticipé
                            app->state = AppStateConnected;
                            reset_text_buffer(app);
                            app->done_tick = 0;
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
                reset_text_buffer(app);
                ttf_bt_send_status("OK\n");
                break;

            // --------------------------------------------------
            case EventTypeSendError:
                if(app->send_thread) {
                    furi_thread_join(app->send_thread);
                    furi_thread_free(app->send_thread);
                    app->send_thread = NULL;
                }
                app->state = AppStateError;
                strncpy(app->error_msg, "HID send failed", TTF_ERROR_MSG_SIZE - 1);
                reset_text_buffer(app);
                ttf_bt_send_status("ERR\n");
                break;

            default:
                break;
            }

            furi_mutex_release(app->mutex);
            view_port_update(app->view_port);

        } else if(status == FuriStatusErrorTimeout) {
            // Timeout 100 ms : transitions automatiques
            furi_mutex_acquire(app->mutex, FuriWaitForever);
            bool need_update = false;

            // Retour auto depuis AppStateDone
            if(app->state == AppStateDone && app->done_tick != 0) {
                if(furi_get_tick() - app->done_tick > TTF_DONE_AUTO_MS) {
                    app->state    = AppStateConnected;
                    app->done_tick = 0;
                    need_update   = true;
                }
            }

            // Dès que l'USB HID est détecté en attente de branchement → lancer l'envoi
            if(app->state == AppStateWaitingUSB && furi_hal_hid_is_connected()) {
                app->state = AppStateSending;
                furi_mutex_release(app->mutex);
                start_send(app);
                furi_mutex_acquire(app->mutex, FuriWaitForever);
                need_update = true;
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
