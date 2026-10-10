[SmartGate](../../../README.md) / [Documentation](../README.md) / Checking access from a device

[English](device-check.md) · [Русский](../../ru/getting-started/device-check.md)

# Checking access from a device

Profile-specific DNS answers and TLS from the controller are preliminary checks. Service readiness is confirmed by a separate application test from the user's network.


## On this page

- [Before connecting](#before-connecting)
- [Android](#android)
- [Windows](#windows)
- [Linux](#linux)
- [macOS](#macos)
- [iPhone and iPad](#iphone-and-ipad)
- [Confirming that the correct DNS is connected](#confirming-that-the-correct-dns-is-connected)
- [What to check](#what-to-check)
- [Reverting and finishing](#reverting-and-finishing)

## Before connecting

Wait for a successful manual apply and DNS verification in Actions. Use a separate test device and save its previous DNS settings so you can revert. Do not change your only working computer until the new route has been tested.

Open your profile's Setup page on [NextDNS](https://my.nextdns.io/) and select the instructions for the device's OS. Use the address for that specific profile: DoH, DoT, or the supplied configuration profile. A generic NextDNS IP without profile binding does not confirm use of the required rewrites.

The instructions below describe the initial NextDNS option. For another DNS provider, use its supplied addresses and connection method. Replace `PROFILE_ID` with your profile's ID everywhere; you do not need to enter the API key or encryption key on the device.

Follow the Setup instructions manually. Pay attention to browser DNS, VPNs, and applications with their own resolver: they may query a different profile. Do not change several devices at once.

## Android

Save the current Private DNS value on the test device. In settings, find **Network & internet → Private DNS** and select the provider hostname option. The exact path depends on the manufacturer; see [Android help](https://support.google.com/pixelphone/answer/2819583).

From your NextDNS profile's Setup page, copy the DNS-over-TLS hostname into the provider field. This is a name such as `PROFILE_ID.dns.nextdns.io`, not a URL with `https://`. Save the setting and confirm that your profile is connected on the Setup page from this phone.

If the system cannot connect to the provider or internet access is lost, restore the previous value. A DNS connection failure is checked separately from a ChatGPT error. Record whether Wi-Fi or mobile data was used.

## Windows

### Windows 11: built-in DNS-over-HTTPS

1. On your profile's **Setup** page, select Windows and save the DNS server addresses and HTTPS template such as `https://dns.nextdns.io/PROFILE_ID`.
2. Open **Settings → Network & internet**, select the active Ethernet or Wi-Fi connection and its properties. Beside **DNS server assignment**, click **Edit**. Save the previous values; you do not need to change IP address assignment.
3. Select **Manual**, enable IPv4, and enter the preferred and alternate DNS addresses from Setup. For each, select **DNS over HTTPS → On (manual template)** and paste your profile's HTTPS template. Disable fallback to unencrypted DNS so a DoH failure does not silently switch queries.
4. If the connection uses IPv6, configure its DNS using the addresses from Setup with the same profile-specific HTTPS template. Configuring only IPv4 may leave queries using another DNS provider over IPv6.
5. Save changes and perform the [connection check](#confirming-that-the-correct-dns-is-connected). Field names may vary between Windows versions.

**Revert:** restore the previous DNS values for this connection; select **Automatic (DHCP)** only if that was the setting before the test. If needed, clear the DNS cache:

```powershell
ipconfig /flushdns
```

Built-in DoH configuration is described in [Microsoft help](https://support.microsoft.com/en-us/windows/experience/connectivity-networking/essential-network-settings-and-tasks-in-windows).

### Windows 10 or no DoH setting

Open the Windows section on **Setup**, install the NextDNS client suggested there, enter your `PROFILE_ID`, and enable the connection. Windows 10 has no built-in DoH setting. Verify the profile after connecting.

**Revert:** disable the connection in the client, uninstall it if needed, and restore the saved DNS settings. Entering a DNS IP without profile binding does not replace this method.

## Linux

For Ubuntu/Debian and other supported distributions, the [official NextDNS client](https://github.com/nextdns/nextdns/wiki/) is a convenient option: it sends system DNS queries over HTTPS. This is a DNS client on the test device; SmartGate itself continues to run in Actions.

1. Save the current DNS settings and use the [instructions for your distribution](https://github.com/nextdns/nextdns/wiki/). A [separate guide](https://github.com/nextdns/nextdns/wiki/Debian-Based-Distribution) is available for Ubuntu/Debian.
2. Download and run the official installer. It installs a third-party client and may request administrator privileges:

   ```bash
   curl -fsSL https://nextdns.io/install -o /tmp/nextdns-install.sh
   sh /tmp/nextdns-install.sh
   ```

3. Select installation, enter your `PROFILE_ID`, and allow the client to handle system DNS on this device. Router configuration mode is not needed to test a computer.
4. Perform the [connection check](#confirming-that-the-correct-dns-is-connected). If NetworkManager or systemd-resolved manages the network, follow the installer's notes for your system; there is no need to edit `/etc/resolv.conf` blindly.

**Revert:** deactivate the client and stop it:

```bash
sudo nextdns deactivate
sudo nextdns stop
```

To uninstall completely, run the installer again and select **Uninstall**, then check the previous DNS settings. Simply stopping the service does not prevent it from starting after a reboot.

## macOS

1. In your profile's **Setup**, select macOS and configuration profile creation, or open the [official generator](https://apple.nextdns.io/). Enter your `PROFILE_ID` in **Profile** and download the `.mobileconfig` file.
2. Open the downloaded file, then **System Settings → General → Device Management**. In the downloaded profiles section, select NextDNS, review its settings, and install the profile. The path may differ in older macOS versions.
3. Perform the [connection check](#confirming-that-the-correct-dns-is-connected) and the selected service's scenarios.

**Revert:** remove the profile added specifically for the test in the same section, restore the previous settings if needed, and check the connection. Installation details are provided in the [NextDNS instructions for Apple](https://apple.nextdns.io/).

## iPhone and iPad

1. In Safari, open your profile's **Setup** page or the [NextDNS profile generator](https://apple.nextdns.io/), enter your `PROFILE_ID`, and download the `.mobileconfig` file.
2. Open **Settings → Profile Downloaded** and install the profile after reviewing its contents. If this option does not appear, find the profile in **General → VPN & Device Management**. Installation may require the device passcode.
3. Perform the [connection check](#confirming-that-the-correct-dns-is-connected) first over Wi-Fi, then separately over the mobile network if you use it. The profile is designed to work across networks; VPNs and network settings may affect the DNS actually used.

**Revert:** in **Settings → General → VPN & Device Management**, select the NextDNS profile you added and remove it, then check the previous connection. [Installing and removing profiles: Apple](https://support.apple.com/en-gb/guide/iphone/iph6c493b19/ios).

## Confirming that the correct DNS is connected

Open your profile's **Setup** page from the device being tested: it must confirm that your specific configuration is in use. You can also open [NextDNS diagnostics](https://test.nextdns.io/); the result contains information about the current connection.

A browser check confirms that browser's DNS path. To check the system setting through a browser, configure it to use system DNS. If the browser instead uses its own DoH, even with the same profile, the result confirms only its connection. VPNs and individual applications may use another DNS provider; the required application must be tested separately.

If the page shows another profile or no profile binding, first fix the connection. A site opening successfully does not by itself prove that it works through SmartGate.

## What to check

Record the date, device/OS, browser or application, network type, and active relay provider, then check:

| Scenario | Result |
| --- | --- |
| The NextDNS profile is connected and responding | Not checked |
| The ChatGPT page loads | Not checked |
| Account login | Not checked |
| Sending a message and streaming the response | Not checked |
| Uploading a file | Not checked |
| Receiving/downloading a file | Not checked |

Use your own account yourself. Do not include cookies, passwords, or private file contents in the report. A small neutral document is suitable for the file test.

If a failure occurs, record the scenario, time, and error hostname if visible. Successful root TLS does not prove that authentication and resources on other domains work correctly. HTTP 403 from an automated request without an account is also not equivalent to a browser test.

## Reverting and finishing

If access is disrupted, restore the saved device settings and check the previous resolver. DNS caches and open connections may retain the previous IP; repeat the test after refreshing them using OS tools.

Automatic application is enabled separately only after manual apply, a repeat no-op run, DNS verification, and all required application scenarios. Until then, `SMARTGATE_APPLY_ENABLED` remains `false`.

---

[All documents](../README.md) · [Home](../../../README.md) · [Actions setup](../operations/actions.md) · [Troubleshooting](../operations/runbook.md)
