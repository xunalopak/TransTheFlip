# TransTheFlip — BLE Remote Bad USB

Envoie du texte depuis un PC maître vers un Flipper Zero via Bluetooth LE.
Le Flipper le tape automatiquement via USB HID sur le PC cible.

```
[PC Maître] ──BLE NUS──▶ [Flipper Zero] ──USB HID──▶ [PC Cible]
 trans_client.py            TransTheFlip.fap           (clavier)
```

## Fonctionnement

1. Branche le Flipper en **USB** sur le **PC cible**
2. Lance l'app TransTheFlip sur le Flipper
3. **Appaire** le Flipper avec le **PC maître** (Windows) — voir ci-dessous.
   Indispensable : les caractéristiques BLE sont protégées par authentification.
4. Démarre `trans_client.py` sur le **PC maître**
5. Saisis ton texte dans le client — il arrive sur l'écran du Flipper
6. Appuie sur **OK** (bouton central) → le Flipper tape le texte sur le PC cible
7. Appuie sur **Retour** pour annuler

> ⚠️ **Appairage obligatoire.** Le service serial du Flipper exige une liaison
> chiffrée et bondée. Le client tente l'appairage automatiquement
> (`client.pair()`), mais si ça échoue : **Paramètres Windows > Bluetooth et
> appareils > Ajouter un appareil**, sélectionne le Flipper, puis **confirme le
> code à 6 chiffres affiché sur l'écran du Flipper**. Une fois bondé, relance le
> client. Sans appairage, la connexion réussit mais **aucune donnée n'est
> transmise** (le compteur `RX:N` du Flipper reste à 0).

## Syntaxe des touches spéciales

Insère des tags `[TAG]` directement dans la chaîne de texte :

| Tag | Touche |
|-----|--------|
| `[ENTER]` | Entrée |
| `[TAB]` | Tabulation |
| `[ESC]` | Échap |
| `[BACKSPACE]` | Retour arrière |
| `[DEL]` | Supprimer |
| `[UP]` `[DOWN]` `[LEFT]` `[RIGHT]` | Flèches |
| `[HOME]` `[END]` `[PGUP]` `[PGDN]` | Navigation |
| `[F1]`..`[F12]` | Touches de fonction |
| `[CTRL+c]` | Ctrl+C |
| `[ALT+F4]` | Alt+F4 |
| `[WIN+r]` | Win+R (Exécuter) |
| `[CTRL+SHIFT+ESC]` | Gestionnaire des tâches |
| `[DELAY:500]` | Pause 500 ms |

**Exemples :**
```
Hello World[ENTER]
[WIN+r]notepad[ENTER]
[CTRL+a][DEL]
ipconfig /all[ENTER]
net user[ENTER][DELAY:1000][ALT+F4]
```

## ⚠️ Layout clavier QWERTY

Le mapping ASCII → HID est basé sur **QWERTY US**.  
Si le PC cible est configuré en **AZERTY**, les caractères spéciaux
(`@`, `&`, `é`, etc.) seront incorrects.

**Solution :** bascule temporairement le PC cible en QWERTY
(Windows : `Win+Espace` pour changer de layout) avant d'envoyer.

> Support AZERTY natif prévu : il suffira d'ajouter une table
> `azerty_hid_table[95]` dans `trans_the_flip_hid.c` et un paramètre
> de sélection de layout.

## Prérequis

### Flipper Zero
- Firmware : **Unleashed** ou **Momentum** (testé)
- **ufbt** installé : `pip install ufbt`

### PC Maître (client Python — Windows)
- Windows 10/11 avec **Bluetooth LE** activé
- Python ≥ 3.9
- `pip install -r pc_client/requirements.txt`
- Flipper **appairé** au préalable (Paramètres > Bluetooth et appareils)

## Build & Installation (Flipper)

```bash
# 1. Installer ufbt
pip install ufbt

# 2. Depuis le dossier du projet
cd TransTheFlip
ufbt update --channel=release   # télécharge le SDK Unleashed

# 3. Compiler
ufbt build

# 4. Déployer (Flipper branché en USB)
ufbt launch
```

L'app apparaît dans le menu **GPIO** du Flipper.

## Utilisation du client Python

```bash
cd pc_client
pip install -r requirements.txt
python trans_client.py
```

```
🔍  Scan BLE en cours (6s)...
✅  Trouvé : Flipper Zero  (AA:BB:CC:DD:EE:FF)
🔗  Connexion à Flipper Zero...
✅  Connecté ! MTU=247
🔐  Appairage établi (liaison chiffrée).

=======================================================
  TransTheFlip Client — Flipper Zero BLE Remote HID
=======================================================
  Syntaxe  : texte normal + [TAGS] pour touches spéciales
  Exemples : Hello[ENTER]   [WIN+r]notepad[ENTER]
=======================================================

> [WIN+r]
📤  Envoyé (7 octets). Confirmez sur le Flipper (bouton OK).
📥  Flipper : texte reçu, en attente de confirmation...
> ✅  Flipper : texte envoyé avec succès
```

## Structure du projet

```
TransTheFlip/
├── application.fam          Manifeste FAP
├── trans_the_flip.c         Point d'entrée, machine d'états, boucle principale
├── trans_the_flip.h         Structs, enums, constantes
├── trans_the_flip_hid.{c,h} Table ASCII→HID, parseur [TAGS], envoi USB HID
├── trans_the_flip_bt.{c,h}  Callbacks BLE serial (NUS)
├── trans_the_flip_view.{c,h} Rendu GUI (ViewPort)
└── pc_client/
    ├── trans_client.py      Client BLE Python (bleak)
    └── requirements.txt
```

## Architecture technique

### BLE : service serial propriétaire Flipper Zero
Le Flipper n'expose **pas** le NUS standard mais son propre service serial :
- **Service UUID** : `8fe5b3d5-2e7f-4a98-2a48-7acc60fe0000`
- **RX** (PC→Flipper, Write) : `19ed82ae-ed21-4c9d-4145-228e62fe0000`
- **TX** (Flipper→PC, Notify) : `19ed82ae-ed21-4c9d-4145-228e63fe0000`
- Terminateur de message : `\n` (newline)
- Messages de statut Flipper→PC : `RECV`, `OK`, `ERR`, `CANCEL`

> 🔐 **Authentification requise.** Les caractéristiques RX/TX sont déclarées
> `ATTR_PERMISSION_AUTHEN` côté firmware : la liaison doit être **appairée et
> chiffrée**. Le client appelle `client.pair()` puis écrit en mode *avec réponse*
> (`response=True`) pour qu'un éventuel rejet de sécurité remonte une erreur au
> lieu d'échouer silencieusement.

### USB HID
- Le Flipper bascule en mode **clavier USB HID** au démarrage de l'app
- L'envoi se fait dans un thread séparé pour garder l'UI réactive
- Délai de 12 ms entre chaque frappe (anti-perte de caractères)
- La config USB d'origine est restaurée à la fermeture

### Machine d'états
```
WaitingBT → Connected → TextReceived → Sending → Done → Connected
                ↑____________________________________________↑
```

## Dépannage

| Problème | Solution |
|----------|----------|
| **Connecté mais `RX:N` reste à 0 / pas de bouton OK** | **Flipper non appairé.** Appaire-le dans Paramètres Windows > Bluetooth et confirme le code sur l'écran du Flipper, puis relance le client. |
| Flipper non trouvé | Vérifie BLE activé + app lancée |
| Caractères incorrects | PC cible en QWERTY (voir section layout) |
| USB non reconnu | Relance l'app (restaure config USB) |
| `furi_hal_bt_serial.h` introuvable | Vérifie version SDK Unleashed/Momentum |

## Licence

**GNU GPLv3** — Copyright © 2026 Romain Champliau — voir le fichier [LICENSE](LICENSE).

Tu peux utiliser, étudier, modifier et redistribuer ce projet, mais **toute
version dérivée doit rester open source sous GPLv3** et conserver l'attribution
à Romain Champliau. Personne ne peut en faire un produit propriétaire fermé.

> ⚠️ Usage éducatif et tests de sécurité autorisés uniquement.
> N'utilise pas cet outil sur des systèmes sans autorisation explicite.
