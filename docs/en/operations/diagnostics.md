[SmartGate](../../../README.md) / [Documentation](../README.md) / Diagnostics

[English](diagnostics.md) · [Русский](../../ru/operations/diagnostics.md)

# Failure reasons in Summary

Summary shows status, operation and DNS check counts, and a **What happened** table: a safe reason code, explanation and next step. Domains, IPs, profile identifiers and API response text remain in the encrypted report.

| Code | What happened | What to do |
| --- | --- | --- |
| `source_unavailable` | The relay address source could not be fetched or validated | Repeat preview; if it recurs, inspect the source in the detailed report |
| `required_route_unavailable` | A required domain has no usable verified route | Run doctor and check the service on a device; do not work around it by raising limits |
| `availability_below_threshold` | Service route availability is below 85% | Review doctor results and route details |
| `dns_api_unavailable` | The DNS provider API request did not complete | Check provider status and keys; check for a pending journal before apply |
| `dns_verification_failed` | DNS responses failed expected checks | Repeat doctor after a short pause; follow recovery for a pending journal |
| `verification_incomplete` | Doctor could not confirm the entire saved configuration | Compare preview and the journal against first-run or recovery instructions |
| `state_conflict` | The state version changed or a lock is held | Wait for the other run, then repeat doctor; do not delete the state branch |
| `config_invalid` | Configuration is invalid or required settings are missing | Run validate and compare settings and Secrets with the guide |
| `mutation_limit` | Too many changes in one run | Check the plan and `max_mutations`; raise the limit only for a reviewed plan |
| `change_ratio_limit` | Too large a share of managed records is changing | Check the plan and `max_change_ratio`; confirm that addresses are actually changing |
| `record_limit` | The managed record limit is exceeded | Reduce domain sets; the hard limit is 100 |
| `plan_conflict` | Record or ownership conflict | Investigate the detailed plan; do not delete foreign records or ownership |
| `recovery_required` | A previous operation is unfinished or its result is unconfirmed | Review the journal and complete recovery before another update |
| `safety_blocked` | A command safety condition is unmet | Check parameters and the detailed report; do not disable protection just to obtain a green status |
| `encryption_setup_invalid` | The protected report could not be prepared | Check the encryption key and Actions context; preserve the original state key |
| `encryption_report_failed` | The report could not be safely encrypted | Check encryption and the journal before the next apply; a plaintext detailed report is not published |
| `interrupted` | The command was interrupted | Repeat a read-only check; if writes were authorized, check the journal first |
| `controller_error` | The reason cannot be classified safely | Read the encrypted report on a trusted device and follow the runbook |

Several reasons can appear together: for example, an unavailable source and a missing required route. Unknown messages are neither published nor classified using words from an external response. If an error occurs before encryption is prepared, there may be no detailed artifact: start with the safe next step in Summary.

These explanations do not change exit codes or successful deployment criteria. A confirmed sync with permitted optional warnings remains successful; conflicts, unconfirmed DNS responses or unfinished operations remain failures.

## When a site stops loading despite a green status

A DNS log entry confirms that the device asked for an address. It does not confirm that the HTTPS connection, sign-in or message succeeded. A relay's status page may check its DNS endpoint or a different network path; it does not establish that every target site is reachable from your network.

1. Record the time, duration and exact browser error. Check whether unrelated sites still work.
2. Compare Wi-Fi with mobile data using the same DNS profile. Two devices on the same Wi-Fi still share a network path.
3. Run **doctor** with `apply=false` and `bootstrap=false` in Actions. Compare its Summary with the last scheduled run. Do not reset state or raise limits to obtain a green status.
4. Inspect the encrypted probe results: compare the currently published relay with other candidates, including failure time, TLS status and reason. A timeout alone does not distinguish server overload from a network problem.
5. If DNS and TLS checks succeed but the site still fails, test it in the browser. A successful TLS handshake confirms a connection and certificate, not application health. An automated HTTP request may receive a Cloudflare challenge even when a browser works.

The default six-hour schedule is periodic maintenance, not continuous outage detection. A short outage may start and recover between runs. Delayed TLS confirmation reduces unnecessary address changes after transient probe failures; it cannot prevent relay outages. Switching most records at once can also be blocked by the change safeguards and needs a reviewed plan. A permanent reduction in this dependency requires independent relay infrastructure and separately tested failover.

[Handling failures](runbook.md) · [Report encryption](privacy.md) · [Status criteria](actions.md)
