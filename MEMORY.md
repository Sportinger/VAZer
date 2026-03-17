# Project Memory

## Arbeitsregeln

- Nach jeder abgeschlossenen Aenderung werden die betroffenen Feature-Docs in `docs/` aktualisiert.
- Nach jeder abgeschlossenen Aenderung wird ein Git-Commit erstellt.
- Nach jedem Commit wird automatisch gepusht, sobald ein Git-Remote konfiguriert ist und der Push technisch moeglich ist.
- Git-Operationen werden immer ueber die Git-CLI ausgefuehrt.
- Standard-Staging laeuft ueber `git add -A`, sofern lokale Temp-Artefakte korrekt in `.gitignore` ausgeschlossen sind.
- Der Default-Branch im GitHub-Remote bleibt geschuetzt.
- Wenn kein Remote konfiguriert ist oder ein Push scheitert, wird das im Abschluss klar benannt.
