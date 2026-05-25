#include "trans_the_flip.h"
#include "trans_the_flip_hid.h"
#include "trans_the_flip_bt.h"
#include "trans_the_flip_view.h"

#include <furi.h>
#include <furi_hal.h>
#include <gui/gui.h>
#include <string.h>
#include <stdlib.h>

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
                            app->state = AppStateSending;
                            // Lance le thread d'envoi (sous mutex pour cohérence)
                            // ATTENTION : on libère le mutex avant de démarrer le thread
                            // pour éviter un deadlock si le thread tente d'acquérir le mutex.
                            furi_mutex_release(app->mutex);
                            start_send(app);
                            furi_mutex_acquire(app->mutex, FuriWaitForever);
                        } else if(app->state == AppStateError) {
                            // OK depuis l'écran d'erreur → retour
                            app->state = AppStateWaitingBT;
                            strncpy(app->error_msg, "", 1);
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
            // Timeout 100 ms : retour automatique depuis AppStateDone
            furi_mutex_acquire(app->mutex, FuriWaitForever);
            bool need_update = false;

            if(app->state == AppStateDone && app->done_tick != 0) {
                if(furi_get_tick() - app->done_tick > TTF_DONE_AUTO_MS) {
                    app->state    = AppStateConnected;
                    app->done_tick = 0;
                    need_update   = true;
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
