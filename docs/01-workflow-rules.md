# Workflow-Regeln

## Ziel

Die Repo-Arbeitsweise soll konsistent bleiben: Features werden dokumentiert, Aenderungen versioniert und nach Moeglichkeit direkt ins Remote gespiegelt.

## Regeln

1. Vor Abschluss eines Features oder einer relevanten Aenderung werden die betroffenen Docs in `docs/` aktualisiert.
2. Feature-Doku soll nicht nur die Idee beschreiben, sondern auch getroffene Entscheidungen, Scope-Aenderungen und offene Punkte.
3. Nach Abschluss meiner Aenderungen erstelle ich immer einen Git-Commit.
4. Nach dem Commit pushe ich automatisch, sobald ein Git-Remote vorhanden ist und der Push erfolgreich ausgefuehrt werden kann.
5. Git-Operationen werden immer ueber die Git-CLI ausgefuehrt.
6. Falls kein Remote existiert oder ein Push fehlschlaegt, wird das explizit gemeldet.

## Praktische Folge fuer dieses Repo

- `docs/` ist die Quelle fuer Feature- und Architektur-Notizen.
- `MEMORY.md` haelt dauerhafte Arbeitsregeln fuer die Zusammenarbeit fest.
