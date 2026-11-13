#!/bin/bash
# After-install script of the .deb packages (electron-builder.config.cjs, deb.afterInstall).
# electron-builder replaces each dollar sign followed by a name in braces with the program name or the
# folder under /opt, and stops on names it does not know, so shell variables here have no braces.
#
# This is electron-builder 26's after-install.tpl with one change: chrome-sandbox is always made
# root-owned with the setuid bit. electron-builder sets that bit only when root cannot create a
# user namespace, but this script runs as root, and the limits that matter apply to ordinary users
# only (Ubuntu 24.04's AppArmor rule, Debian's kernel.unprivileged_userns_clone=0). Without the bit
# the start script (scripts/linux-sandbox.cjs) would start the program with --no-sandbox whenever
# the AppArmor profile below is not loaded. Chromium still prefers user namespaces when it can use
# them, so where the profile is loaded the helper stays unused.

if type update-alternatives 2>/dev/null >&1; then
    # Remove previous link if it doesn't use update-alternatives
    if [ -L '/usr/bin/${executable}' -a -e '/usr/bin/${executable}' -a "`readlink '/usr/bin/${executable}'`" != '/etc/alternatives/${executable}' ]; then
        rm -f '/usr/bin/${executable}'
    fi
    update-alternatives --install '/usr/bin/${executable}' '${executable}' '/opt/${sanitizedProductName}/${executable}' 100 || ln -sf '/opt/${sanitizedProductName}/${executable}' '/usr/bin/${executable}'
else
    ln -sf '/opt/${sanitizedProductName}/${executable}' '/usr/bin/${executable}'
fi

# Chromium's setuid sandbox helper: owned by root, mode 4755.
chown root:root '/opt/${sanitizedProductName}/chrome-sandbox' || true
chmod 4755 '/opt/${sanitizedProductName}/chrome-sandbox' || true

if hash update-mime-database 2>/dev/null; then
    update-mime-database /usr/share/mime || true
fi

if hash update-desktop-database 2>/dev/null; then
    update-desktop-database /usr/share/applications || true
fi

# Install apparmor profile. (Ubuntu 24+)
# The profile gives the start script (/opt/${sanitizedProductName}/${executable}) the userns permission. It runs in
# unconfined mode, so the program the script starts stays under it and can create the user
# namespaces Chromium's sandbox uses.
# First check if the version of AppArmor running on the device supports our profile.
# This is in order to keep backwards compatibility with Ubuntu 22.04 which does not support abi/4.0.
# In that case, we just skip installing the profile since the app runs fine without it on 22.04.
#
# Those apparmor_parser flags are akin to performing a dry run of loading a profile.
# https://wiki.debian.org/AppArmor/HowToUse#Dumping_profiles
if apparmor_status --enabled > /dev/null 2>&1; then
  APPARMOR_PROFILE_SOURCE='/opt/${sanitizedProductName}/resources/apparmor-profile'
  APPARMOR_PROFILE_TARGET='/etc/apparmor.d/${executable}'
  if apparmor_parser --skip-kernel-load --debug "$APPARMOR_PROFILE_SOURCE" > /dev/null 2>&1; then
    cp -f "$APPARMOR_PROFILE_SOURCE" "$APPARMOR_PROFILE_TARGET"

    # Updating the current AppArmor profile is not possible and probably not meaningful in a chroot'ed environment.
    # Use cases are for example environments where images for clients are maintained.
    # There, AppArmor might correctly be installed, but live updating makes no sense.
    if ! { [ -x '/usr/bin/ischroot' ] && /usr/bin/ischroot; } && hash apparmor_parser 2>/dev/null; then
      # Extra flags taken from dh_apparmor:
      # > By using '-W -T' we ensure that any abstraction updates are also pulled in.
      # https://wiki.debian.org/AppArmor/Contribute/FirstTimeProfileImport
      apparmor_parser --replace --write-cache --skip-read-cache "$APPARMOR_PROFILE_TARGET"
    fi
  else
    echo "Skipping the installation of the AppArmor profile as this version of AppArmor does not seem to support the bundled profile"
  fi
fi
