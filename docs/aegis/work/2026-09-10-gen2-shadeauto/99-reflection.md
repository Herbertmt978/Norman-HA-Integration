# Result and compatibility decision

One distributed component now selects a protocol during setup. The legacy domain
and missing-generation default retain installed Gen 1 identities and behavior.
Gen 2 transport and entities remain inside a separately attributed backend;
reusing the Gen 1 transport would have conflated different APIs and credentials.
No legacy protocol or persisted identity was retired.

Verification caught imported typing incompatibilities and tests that assumed one
HA naming convention. The final test uses the registry identity across versions.
Real Gen 2 HTTP fixture checks supplement mocks, without claiming hardware proof.

No scope drift: no production changes, publication or new MCP configuration.
The remaining limitation is physical ShadeAuto qualification, requiring a real hub.
