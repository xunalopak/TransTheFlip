# Mise à jour du client PC et du Flipper

**Correctif v2.0.2 :** suppression du double affichage des commandes au bas de
l'écran Flipper. L'historique affiche trois lignes sans recouvrir le pied d'écran,
et l'indication `Up:Log` ne recouvre plus le message central. Remplacer le FAP ;
le client PC v2.0.1 reste compatible.

**Correctif v2.0.1 :** le client PC écoute maintenant le canal des réponses série
du Flipper (FE61), et non le contrôle de débit (FE63). Cela corrige la déconnexion
après le message « ne répond pas au protocole TTF1 ». Si le FAP v2.0.0 est déjà
installé, seul le client PC doit être remplacé. La connexion a été vérifiée sur Arieda,
avec Momentum mntm-012, sans envoyer de texte ni de touches USB.

Cette version nécessite **les deux nouvelles applications**. Le transfert utilise
désormais une longueur et un CRC32 ; les anciennes versions ne sont pas compatibles.
Le client refuse l'envoi tant que l'application Flipper n'a pas confirmé sa version.

## Installation

- PC : lancer `dist/TransTheFlip-GUI.exe`. Python n'est pas nécessaire.
- Flipper : copier `dist/trans_the_flip.fap` dans `apps/GPIO` sur la carte SD avec
  qFlipper, puis ouvrir TransTheFlip. Quitter l'application avant la copie : son
  mode clavier USB remplace le port de transfert qFlipper.
- Le FAP local est compilé avec le SDK officiel 1.4.3, API 87.1. En cas d'API
  incompatible sur Momentum/Unleashed, recompiler avec le SDK du firmware installé.
- Cliquer Scan, choisir le Flipper, puis Connect et confirmer l'appairage si demandé.

## Logiciel PC

- Le dernier appareil connecté est mémorisé dans
  `%LOCALAPPDATA%/TransTheFlip/settings.json`. Au prochain démarrage, Connect permet
  de s'y reconnecter sans sélectionner à nouveau l'appareil.
- Les diagnostics distinguent notamment délai dépassé, appareil absent, appairage
  refusé, application incompatible et perte de liaison.
- Entrée ajoute une ligne ; Ctrl+Entrée ou Send envoie le texte.
- La barre suit le transfert Bluetooth, la confirmation sur le Flipper puis la frappe.
  « Terminé » n'apparaît qu'après le retour du Flipper, pas après la dernière écriture BLE.
- Le texte reste dans l'éditeur, même après une erreur. Un envoi en attente empêche
  le suivant. Aucun renvoi automatique après une coupure : vérifier le PC cible avant
  de réessayer, car une partie du texte a pu être tapée.
- L'historique contient les 20 derniers textes dont la frappe a été confirmée.
  Il reste en mémoire et disparaît à la fermeture ; seuls le nom et l'adresse du
  périphérique sont enregistrés sur disque.

## Application Flipper

- L'en-tête indique séparément `BT:ON/OFF` et `USB:Ready/OFF`.
- Avant confirmation, Haut/Bas fait défiler tout le texte. Les retours à la ligne et
  tabulations apparaissent comme `[ENTER]` et `[TAB]` ; les tags restent visibles.
- Droite change le délai après chaque frappe : 8, 25, 50, 100 ou 250 ms. Le maintien
  d'une touche dure 12 ms supplémentaires. Le réglage vaut pour la session.
- OK confirme ; sans USB, l'application attend le branchement au PC cible.
- Pendant la frappe, le pourcentage indique l'avancement dans le texte et les tags.
  Retour arrête l'envoi, y compris pendant un `[DELAY]`, et relâche toutes les touches.
  Une coupure Bluetooth arrête aussi l'envoi ; une coupure USB signale une erreur.
- Un texte trop long, une perte de données, un CRC incorrect ou un transfert incomplet
  après 5 secondes est refusé. Aucun fragment reçu ne devient un texte à confirmer.

La limite reste **255 octets, tags compris**. Le clavier existant accepte l'ASCII,
les tabulations et les retours à la ligne. Les caractères non pris en charge sont
refusés explicitement. Le sélecteur de layout existant reste accessible avec Gauche.

## Construire et vérifier

```powershell
python -m pip install -r pc_client/requirements.txt pyinstaller
./pc_client/build_windows.ps1
ufbt build

# GCC/MinGW doit être dans PATH pour les tests C ; aucun matériel nécessaire.
./tests/run_checks.ps1
```

Les tests C utilisent le récepteur et le moteur de frappe de production avec un
clavier simulé : ils n'envoient aucune touche réelle.
