Control-plane ACL programming must protect FRR loopback TCP ports 2601 (zebra VTY) and 2620 (FPM) in every managed namespace, including host and per-ASIC namespaces. OUTPUT traffic to these loopback ports is accepted for uid-owner 300 and dropped for other users. Inputs include existing control-plane ACL configuration, namespace/ASIC identity and iptables rule lists; observables are the programmed IPv4 OUTPUT rules, while unrelated existing rules are preserved.
Satisfy the existing checks associated with tests/cacl/test_cacl_application.py::test_cacl_application_nondualtor without changing this intended behavior.

## Observation context

The following describes setup and observable shapes, not additional behavioral requirements or expected assertion results.

- Setup: the selected test delegates to verify_cacl with DUT, topology, credentials, and Docker-network fixtures. That helper builds comparison rule collections from configuration and queries iptables -S and ip6tables -S on the selected host/ASIC.
- Observations: command stdout split into rule lines, converted to sets, and set differences between collected and helper-generated rules for both IPv4 and IPv6.
- Output shape: namespace/address-family-specific rule-string collections and missing/unexpected-rule sets. Retain unrelated rules as well as OUTPUT rules; the selected helper does not perform its commented-out positional ordering check. Concrete device configuration remains fixture input, not an invented fixed ruleset.
