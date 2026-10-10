[SmartGate](../../../README.md) / [Documentation](../README.md) / First run

[English](quick-start.md) · [Русский](../../ru/getting-started/quick-start.md)

# First run

### Start SmartGate in 6 steps

The simplest option is **GitHub Actions + NextDNS + GeoHide**. It is already configured in the repository: you do not need your own server to get started. SmartGate will check GeoHide addresses and write suitable routes to **your** DNS profile.

**You will need:** GitHub and NextDNS accounts, a separate empty DNS profile, and a device for testing. The controller runs in GitHub Actions — you do not need to keep a computer switched on. Reading the detailed encrypted plan currently requires Python on a trusted device; see the [decryption instructions](../operations/privacy.md#reading-a-detailed-report).

#### 1. Make your own copy

Click **Fork**, keep **Copy the main branch only** selected, and create the repository. Perform all subsequent steps **in your own copy**. Open **Actions** and enable workflows if GitHub prompts you to do so.

You do not need to copy someone else's `smartgate-state` branch: SmartGate will create its own on the first apply.

#### 2. Prepare a DNS profile

Register with [NextDNS](https://my.nextdns.io/) and create a **separate profile without DNS rewrites** — address substitution rules. Save its **ID** from the **Setup** page and your **API key** from your account settings.

Keep the device's current DNS for now. SmartGate must first prepare and verify the new addresses.

#### 3. Add settings in GitHub

In your copy, open **Settings → Environments → New environment**, name the environment **`smartgate`**, and add:

| Where to add it | Name | Value |
| --- | --- | --- |
| Environment secret | `SMARTGATE_DNS_PROFILE_ID` | Your new DNS profile's ID |
| Environment secret | `SMARTGATE_DNS_API_KEY` | Your NextDNS account's API key |
| Environment secret | `SMARTGATE_STATE_ENCRYPTION_KEY` | Your own encryption key: 32 random bytes in Base64 |
| Environment variable | `SMARTGATE_APPLY_ENABLED` | `false` — scheduled runs only check routes for now |

The encryption key protects state and detailed reports. Prepare it using the [instructions](../operations/privacy.md#prepare-a-key) and save an independent copy in a password manager. The DNS API key and encryption key are **two different secrets**.

The initial configuration already uses NextDNS and GeoHide. This option requires no provider changes in the files.

#### 4. Review what will change

Open **Actions → SmartGate sync → Run workflow**. Select your copy's **default branch** (usually `main`) and these parameters:

| Field | Value |
| --- | --- |
| `command` | `preview` |
| `apply` | `false` — unchecked |
| `bootstrap` | `false` — unchecked |
| `require_no_change` | `false` — unchecked |

When the run finishes, open **Summary**: it shows the outcome, number of planned changes, and availability checks. This run **does not write DNS rules or state**. **Before the first apply**, [download and decrypt the detailed report](../operations/privacy.md#reading-a-detailed-report): check which domains and addresses will be added. Summary only shows aggregate counts, so this report is needed to review the actual plan. Reading the report locally is a separate action; the controller continues to run in GitHub Actions.

**If the first plan exceeds 20 changes:** open [`config/smartgate.actions.toml`](../../../config/smartgate.actions.toml) in your copy and temporarily raise `max_mutations` to a number sufficient for the reviewed plan. For example, **30** is enough for 25 new rules. Save the change on your copy's default branch and repeat `preview` with `apply=false`, `bootstrap=false`. This protects against a large batch of changes; it is not a ChatGPT usage limit.

If there are errors or a block, first investigate the cause using the [Actions instructions](../operations/actions.md#manual-runs).

#### 5. Create DNS rules

After reviewing the plan, open **Run workflow** again on your copy's **default branch**:

| Field | Value |
| --- | --- |
| `command` | `sync` |
| `apply` | `true` — checked |
| `bootstrap` | `true` — only for the first run on an empty profile |
| `require_no_change` | `false` — unchecked |

SmartGate will now create rules in the selected profile, verify DNS answers, and save encrypted state on a separate branch. Wait for a successful result and read the warnings in **Summary**. If the run fails, review the [procedure](../operations/runbook.md) before applying again.

After the first successful apply, restore `max_mutations` to **20** if you raised it. Use **`bootstrap=false`** for subsequent normal synchronizations.

**Verify a repeat run without changes:** select `command=sync`, `apply=true`, `bootstrap=false`, and **`require_no_change=true`**. This mode checks saved state against DNS but does not permit creating, replacing, or deleting DNS rules. Wait for a successful check with zero changes. If a new plan appears, the run will stop: investigate it using the [instructions](../operations/actions.md#checking-without-dns-changes) before enabling the schedule.

#### 6. Connect a device and check access

In NextDNS, open your profile's **Setup** page and follow the device instructions. On Android, the **Private DNS** field uses a hostname such as **`YOUR_PROFILE_ID.dns.nextdns.io`**, without `https://`. A client that supports DNS-over-HTTPS uses **`https://dns.nextdns.io/YOUR_PROFILE_ID`**. Substitute **your own** ID.

Open ChatGPT and check page loading, login, sending a message, uploading a file, and downloading a file. The expected result is that the service works through your DNS profile. See [connection and functionality checks](device-check.md).

After a successful repeat run without changes and the device check, you can enable scheduled route updates: in the `smartgate` environment, change **`SMARTGATE_APPLY_ENABLED` to `true`**. SmartGate will run every 6 hours; GitHub runs may be delayed. With `false`, the schedule runs `doctor`: it checks routes and DNS for managed records without changing DNS or state. Before the first successful bootstrap, such a run may finish as `degraded` because there is nothing to confirm yet; it does not create rules automatically.

**Done:** the DNS provider answers your device, the relay forwards traffic, and SmartGate keeps routes up to date.

<details>
<summary><strong>I want another DNS provider, my own VPS, or more services</strong></summary>

The DNS provider and relay are selected independently. NextDNS is the current quick-start option; the AdGuard Home and Technitium reference adapters are currently read-only and are not ready for production application. Your own compatible relay on a VPS is already supported.

- [Choose a DNS provider](../providers/dns/README.md).
- [Connect your own relay](../operations/migration.md).
- [Enable Claude, Gemini, Spotify, and other service sets](../providers/services.md).

</details>
