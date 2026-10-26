# SPDX-License-Identifier: GPL-3.0-or-later
# Source build of wLib: Vue frontend from ui/package-lock.json plus a Python
# environment from nixpkgs, started through the shared release launcher.
{
  lib,
  stdenv,
  buildNpmPackage,
  importNpmLock,
  nodejs_22,
  python3,
  qt6,
  makeWrapper,
  mesa-demos,
  xdg-utils,
  glib,
  _7zz,
  libarchive,
  bubblewrap,
  wineWowPackages,
  winetricks,
  withWine ? true,
  wine ? wineWowPackages.stable,
}:

let
  fs = lib.fileset;

  # Keep APP_VERSION in core/api.py the single source of truth.
  version = builtins.head (
    builtins.match ''.*APP_VERSION = "([^"]+)".*'' (builtins.readFile ../core/api.py)
  );

  frontend = buildNpmPackage {
    pname = "wlib-ui";
    inherit version;

    src = fs.toSource {
      root = ../ui;
      fileset = fs.difference ../ui (
        fs.unions [
          (fs.maybeMissing ../ui/node_modules)
          (fs.maybeMissing ../ui/dist)
        ]
      );
    };

    nodejs = nodejs_22;
    npmDeps = importNpmLock {
      package = lib.importJSON ../ui/package.json;
      packageLock = lib.importJSON ../ui/package-lock.json;
    };
    npmConfigHook = importNpmLock.npmConfigHook;

    installPhase = ''
      runHook preInstall
      cp -r dist $out
      runHook postInstall
    '';
  };

  pythonEnv = python3.withPackages (ps: [
    ps.pywebview
    ps.qtpy
    ps.pyqt6
    ps.pyqt6-webengine
    ps.certifi
    ps.cryptography
  ]);

  # Suffixed so tools the user installed (their own Wine flavour, say) win.
  runtimePath = lib.makeBinPath (
    [
      mesa-demos # glxinfo for the launcher's GPU probe
      xdg-utils
      glib # gdbus for portals and notifications
      _7zz
      libarchive # bsdtar, also covers .rar
      bubblewrap # game sandbox
    ]
    ++ lib.optionals withWine [
      wine
      winetricks
    ]
  );
in
stdenv.mkDerivation {
  pname = "wlib";
  inherit version;

  src = fs.toSource {
    root = ../.;
    fileset = fs.unions [
      ../main.py
      ../core
      ../extension
      ../packaging/wlib-launcher
      ../wlib.desktop
      ../wlib.png
      ../icon.svg
      ../LICENSE
    ];
  };

  nativeBuildInputs = [
    makeWrapper
    qt6.wrapQtAppsHook
  ];

  buildInputs = [
    qt6.qtbase
    qt6.qtwayland
    qt6.qtwebengine
  ];

  dontBuild = true;
  dontWrapQtApps = true;

  installPhase = ''
    runHook preInstall

    app=$out/share/wlib
    mkdir -p $app/ui
    cp -r main.py core extension wlib.png icon.svg LICENSE $app/
    cp -r ${frontend} $app/ui/dist
    install -m755 packaging/wlib-launcher $app/wlib
    ${pythonEnv.interpreter} -m compileall -q $app/main.py $app/core

    install -Dm644 wlib.desktop $out/share/applications/wlib.desktop
    install -Dm644 icon.svg $out/share/icons/hicolor/scalable/apps/wlib.svg
    install -Dm644 wlib.png $out/share/icons/hicolor/256x256/apps/wlib.png

    runHook postInstall
  '';

  # The launcher runs ./wlib-bin from its own directory after the GPU probe and
  # LD_LIBRARY_PATH restore, so the Qt environment is applied to wlib-bin only.
  postFixup = ''
    app=$out/share/wlib
    makeWrapper ${pythonEnv.interpreter} $app/wlib-bin \
      --add-flags $app/main.py \
      "''${qtWrapperArgs[@]}"

    makeWrapper $app/wlib $out/bin/wlib \
      --set WLIB_APP_DIR $app \
      --set-default WLIB_LAUNCHER_NAME Nix \
      --suffix PATH : ${runtimePath}
  '';

  doInstallCheck = true;
  installCheckPhase = ''
    runHook preInstallCheck
    HOME=$(mktemp -d) WLIB_DATA_DIR=$(mktemp -d) QT_QPA_PLATFORM=offscreen \
      $out/share/wlib/wlib-bin --smoke-test
    runHook postInstallCheck
  '';

  passthru = {
    inherit frontend pythonEnv;
  };

  meta = {
    description = "Linux game manager for F95Zone";
    homepage = "https://github.com/kirin-3/wLib";
    license = lib.licenses.gpl3Plus;
    mainProgram = "wlib";
    platforms = [ "x86_64-linux" ];
  };
}
