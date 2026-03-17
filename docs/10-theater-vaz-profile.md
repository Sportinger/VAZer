# Theater-VAZ Profil

## Scope

VAZer ist vorerst kein generischer Multicam-Editor.

Der aktuelle Produktscope ist hart auf diesen Fall getrimmt:

- Theater-Mitschnitt
- durchgehende Originalreihenfolge
- 2 bis 3 Kameras
- eine saubere Master-Audiospur
- multicam VAZ mit typischer Rollenverteilung aus Totale, Close und oft Halbtotale

## Feste Annahmen

- Der finale Export bleibt immer ein durchgehender Mitschnitt.
- Der finale Ton kommt immer ausschliesslich aus der Masterspur.
- Die Totale ist die sichere Fallback-Kamera.
- Die Totale braucht im Normalfall keine technische Analyse, weil sie statisch und verlaesslich ist.
- `close` ist meist die bevorzugte Kamera, wenn sie technisch gut und dramaturgisch passend ist.
- `halbtotale` ist die Bruecke zwischen textlicher Naehe und Buehnenraum.

## Ziel des Schnitts

Der Schnitt soll:

- den Text und die Buehnensituation verstaendlich halten
- moeglichst oft auf einer guten Close bleiben
- aber nicht auf eine falsche, unscharfe oder unruhige Kamera schneiden
- ein ausgewogenes, nicht hektisches Rhythmusgefuehl behalten

Der Mitschnitt soll sich wie eine theatralisch sinnvolle Dokumentation anfuehlen, nicht wie ein nervoes Live-Multicam-Mixing.

## Kamera-Prioritaet

Grundsaetzliche Prioritaet:

1. `close`, wenn sie scharf, ruhig und textlich passend ist
2. `halbtotale`, wenn mehrere Figuren wichtig sind oder Raumbezug zaehlt
3. `totale`, wenn die engeren Kameras technisch oder dramaturgisch falsch sind

## Text und Schnitte

- Text ist wichtig, aber keine starre harte Schnittgrenze.
- Ein Schnitt darf auch mitten im Wort passieren, wenn das die klar bessere Bildentscheidung ist.
- Wichtiger als Satzgrenzen ist: die richtige Kamera fuer den Moment.
- Die falsche Close auf die falsche Figur ist schlechter als eine gute Halbtotale oder Totale.

## Technische Verbote

Moeglichst nicht:

- in unscharfe Shots schneiden
- in stark bewegte Shots schneiden
- auf Shots bleiben, die waehrend des Halts sichtbar instabil werden
- hektische Hin-und-Her-Wechsel ohne klaren Anlass

## Bevorzugte Bildlogik

`close` aktiv bevorzugen:

- bei wichtigen Textmomenten
- bei klarer Sprecherfuehrung
- bei Ereignissen oder Fokusmomenten

`halbtotale` oder `totale` bevorzugen:

- wenn mehrere Figuren gleichzeitig zaehlen
- bei starker Raumwirkung
- bei Musik, Bewegung, Tableau oder Choreografie
- bei Applaus, Umbau, Schwarzphasen oder Uebergaengen

## Rhythmus

Leitfaden, keine starre Regel:

- laenger als grob `60s` ohne Schnitt ist meist zu traege
- wiederholt Schnitte alle `20s` koennen schnell zu hektisch wirken
- einzelne Ausnahmen sind okay, solange der Gesamtrhythmus ausgewogen bleibt

## Fuer spaetere AI-Planung

Der spaetere LLM-Draft-Planer soll nicht wie ein allgemeiner Videoeditor denken, sondern wie ein Assistent fuer Theater-VAZ:

- textorientiert
- buehnenorientiert
- close-freundlich
- technisch vorsichtig
- mit Totale als vertrauenswuerdiger Sicherheitskamera
