!macro customInstall
  WriteRegStr HKCU "Software\Zaowu\Provider" "InstallDir" "$INSTDIR"
!macroend

!macro customUnInstall
  DeleteRegValue HKCU "Software\Zaowu\Provider" "InstallDir"
!macroend
