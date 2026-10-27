; v34: Die KI-Modelle (Qwen + GLiNER, ca. 3,5 GB) stecken nicht im
; Installer, weil NSIS-Installer hoechstens 2 GB gross sein duerfen.
; Sie liegen im Installationspaket im Ordner "models" neben der setup.exe und
; werden hier bei der Installation dorthin kopiert, wo der Sidecar sie
; sucht ($INSTDIR\sidecar\models, siehe sidecar/core/paths.py).

; Bei einem Update ueberschreibt NSIS nur, alte Dateien bleiben liegen.
; Veraltete Python-Pakete im Sidecar (z.B. torchvision aus v39) werden
; dann trotzdem importiert und bringen den Start zum Absturz. Deshalb den
; Programmteil des Sidecars vorher entfernen - die Modelle in
; sidecar\models bleiben erhalten.
!macro NSIS_HOOK_PREINSTALL
  ${If} ${FileExists} "$INSTDIR\sidecar\_internal\*.*"
    DetailPrint "Entferne alte Sidecar-Dateien ..."
    RMDir /r "$INSTDIR\sidecar\_internal"
  ${EndIf}
!macroend

!macro NSIS_HOOK_POSTINSTALL
  ; Bis v39 nur bei vorhandener Qwen-.gguf - ohne Qwen fehlte dann auch
  ; GLiNER (hf-cache), und selbst der Modus "Schnell" ging nicht.
  ${If} ${FileExists} "$EXEDIR\models\*.gguf"
  ${OrIf} ${FileExists} "$EXEDIR\models\hf-cache\*.*"
    DetailPrint "Kopiere KI-Modelle, das dauert einen Moment ..."
    CreateDirectory "$INSTDIR\sidecar\models"
    CopyFiles /SILENT "$EXEDIR\models\*.*" "$INSTDIR\sidecar\models"
  ${ElseIf} ${FileExists} "$INSTDIR\sidecar\models\hf-cache\*.*"
    DetailPrint "KI-Modelle sind schon installiert."
  ${Else}
    MessageBox MB_OK|MB_ICONEXCLAMATION "Der Ordner $\"models$\" wurde neben dem Installer nicht gefunden.$\r$\nDie App startet; manuelles Schwaerzen und der Modus $\"Ohne KI$\" funktionieren,$\r$\ndie KI-Erkennung aber nicht.$\r$\nBitte den kompletten Ordner des Installationspakets entpacken und den Installer von dort starten."
  ${EndIf}

  ; Phase 5: Kunden-Branding aus dem Ordner "branding" neben der setup.exe
  ; (Logo, Namen, Akzentfarbe - siehe sidecar/core/branding.py). Liegt keiner
  ; daneben, bleibt ein vorhandenes Branding bei einem Update erhalten.
  ${If} ${FileExists} "$EXEDIR\branding\branding.json"
    DetailPrint "Uebernehme Kunden-Branding ..."
    RMDir /r "$INSTDIR\sidecar\branding"
    CreateDirectory "$INSTDIR\sidecar\branding"
    CopyFiles /SILENT "$EXEDIR\branding\*.*" "$INSTDIR\sidecar\branding"
  ${EndIf}
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  RMDir /r "$INSTDIR\sidecar\models"
  RMDir /r "$INSTDIR\sidecar\branding"
!macroend
