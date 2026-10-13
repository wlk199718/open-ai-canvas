!macro customCheckAppRunning
  InitPluginsDir
  File /oname=$PLUGINSDIR\zaowu-installer-processes.ps1 "${BUILD_RESOURCES_DIR}\..\..\desktop-shared\installer-processes.ps1"
  ReadRegStr $R8 SHELL_CONTEXT "${INSTALL_REGISTRY_KEY}" "InstallLocation"
  nsExec::ExecToStack /TIMEOUT=40000 `"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\zaowu-installer-processes.ps1" -InstallDir "$INSTDIR" -PreviousInstallDir "$R8" -AppExecutable "${APP_EXECUTABLE_FILENAME}"`
  Pop $R0
  Pop $R1
  ${If} $R0 != 0
    MessageBox MB_OK|MB_ICONEXCLAMATION "无法关闭旧版造物，请关闭客户端和后台进程后重试。安装尚未覆盖程序文件。" /SD IDOK
    Abort
  ${EndIf}
  # Old NSIS uninstallers rename files into their temp directory during upgrade.
  # Keep that directory on the installation drive so Rename never crosses drives.
  StrCpy $R9 "$R8"
  ${If} $R9 == ""
    StrCpy $R9 "$INSTDIR"
  ${EndIf}
  ${GetParent} "$R9" $R9
  # Keep the prefix short: legacy uninstallers use Win32 Rename with MAX_PATH.
  StrCpy $R0 "${APP_GUID}" 8
  StrCpy $R9 "$R9\.zu-$R0"
  ClearErrors
  CreateDirectory "$R9"
  ${If} ${Errors}
    MessageBox MB_OK|MB_ICONEXCLAMATION "无法创建升级临时目录，请选择可写的安装位置。" /SD IDOK
    Abort
  ${EndIf}
  System::Call 'kernel32::SetEnvironmentVariable(t "TEMP", t "$R9")'
  System::Call 'kernel32::SetEnvironmentVariable(t "TMP", t "$R9")'
!macroend

!macro customInit
  # Assisted silent upgrades elevate before installSection, which skips
  # CHECK_APP_RUNNING in the inner instance. Run it after elevation as well.
  ${If} ${UAC_IsInnerInstance}
    !insertmacro customCheckAppRunning
  ${EndIf}
!macroend
