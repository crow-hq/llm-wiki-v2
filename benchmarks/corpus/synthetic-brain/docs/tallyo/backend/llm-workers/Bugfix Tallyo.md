# Bugfix Tallyo

L'app non sincronizza spese HEIC; il backend riceve dati corrotti e Mistral 4.1 fallisce. Fix: HEIC-to-JPEG conversion in Cloud Functions, validare EXIF per evitare duplicati.

