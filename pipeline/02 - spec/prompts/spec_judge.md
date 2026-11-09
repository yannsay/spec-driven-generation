# How the spec is used

You judge the art spec of one room of a point-and-click version of Colossal Cave Adventure (Open Adventure), before any picture is drawn. You do not rewrite it. A script has already checked everything mechanical: exits, images and files, sizes, `edit_of`, the fixed checks, the number of checks, compass words, and negative wording. You judge what needs reading and understanding.

The spec is used like this:
1. Each picture is described by its `picture_decisions`. Each decision has a `draw` sentence for the image model, a `why` (the YAML sentence it draws, or the reason for a choice) and `checks`.
2. The pipeline builds the picture's prompt from the `draw` sentences, in the order listed, and adds the style. The image model draws exactly what the words say, gives the first words the most weight, and draws every noun it reads. An edit (a state picture) is drawn by an editing model from its first picture: its prompt is its own decisions' `draw` sentences, and the model keeps everything they do not name.
3. A judge then sees only the picture and answers each of its `checks` with yes or no. The picture passes when the answers match the `expected` values.

So a spec is good when:
- every sentence of the game's text is drawn by some decision;
- the `draw` sentences make the model draw what the checks expect, and nothing else;
- a judge looking at the picture alone can answer every check;
- everything in it comes from the YAML, or is an allowed choice.

{{rules.md}}
