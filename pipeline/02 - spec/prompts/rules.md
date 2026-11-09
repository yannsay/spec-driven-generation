# The rules

The rules every spec is written under. They are addressed to the spec writer ("you"); the spec judge, the repair and the picture router read the same text.

## Image format

The game runs as a phone app and as a web page, so one image must work on every screen.
- **Scenes** are landscape 16:9, 2048×1152, seen in first person: the camera is at standing eye height, inside the room, looking at it. No player figure, no hands.
- **Safe area.** Wider screens crop the top and bottom, narrower ones the sides. Every exit and every open stretch of floor where an item could be laid stays inside the central 75% of the width and 80% of the height. The edges hold scenery only.
- **Items** are square, 1024×1024: one object alone, centred and whole. Several pieces are one object when together they are the one thing the YAML names (three apples for "some fruit", a hammer and tongs for "some tools"), shown together; a container or holder is not part of the group. They appear in the inventory and can be dropped in any room, so they never match one room's angle or light: a neutral three-quarter view, soft even light. The pipeline gives items a background of their own; describe the object only.

## 1. Which exits to draw

Go through `travel` and keep only entries whose action is `goto`. Merge entries that go to the same room.
- Draw an exit when at least one of its verbs is a walking direction: NORTH, SOUTH, EAST, WEST, NE, NW, SE, SW, UPWAR, DOWN, DOWNW, INWAR, OUT, OUTDO, OUTSI, ENTER, EXIT, CRAWL, CLIMB, FORWA, BACK, LEFT, RIGHT, JUMP, or a word naming something walkable that the description mentions (a road, a forest, a gully, a stair...). STREA, UPSTR and DOWNS (following the water) count whenever the room has running water, such as a stream or a spring.
- Do not draw an exit whose verbs are only magic words or names of places not in the description. Those are shortcuts, not paths. Where an exit leads does not decide whether it is drawn: a way that turns out too narrow to pass is still drawn.
- Two exits that share a verb, one of them with a `cond` (UP to the tower, but UP while carrying the anchor leads somewhere else), are the same way in the picture. Draw it once, for the exit without the `cond`. The other gets `drawn: false`, with the reason "same way as <room> when <cond>".
- List every `goto` exit in `exits`, drawn or not, with the reason.
- **Where a drawn exit sits comes from its compass direction.** The player taps the picture as a map: north is ahead, south is the near foreground, east is the right, west is the left. When the exit's verbs hold exactly one compass direction, draw it at that place and use these exact words in its `label` and in its `draw` sentence:

  | Verb | Words |
  |---|---|
  | NORTH | straight ahead |
  | SOUTH | in the foreground |
  | EAST | on the right |
  | WEST | on the left |
  | NE | ahead on the right |
  | NW | ahead on the left |
  | SE | in the foreground on the right |
  | SW | in the foreground on the left |

  The compass always wins. The camera faces north in every room, even when the description is written from another direction ("a hall stretching forward to the west", "a dome behind you"): draw each way at its compass place and describe it from there ("the hall stretches away on the left"). Side words in the verbs (LEFT, RIGHT, FORWA, BACK) do not change the place.

  An exit with no compass direction (UP, DOWN, IN, OUT, a place word) or with more than one may sit anywhere that keeps the exits apart. Each drawn exit stays visible and distinct from the others.
- Draw each exit as a thing the YAML supports: an arch in a wall it mentions, a bridge it names, a river's bank. Do not invent stairs, ladders or tunnels to make a way visible.
- **An exit shows the start of where it leads**, from that room's description under `# where each exit leads`. When this room's text names nothing at that place, draw the edge of the next place: an exit to a marsh is reeds and standing water at the edge of the frame, an exit to a meadow is open grass running out of the picture. Its label names what is seen ("reeds and standing water on the left").
- The `label` of a drawn exit describes what the player sees and taps, and ends with its compass place when it has one: "a rope bridge on the right" for an EAST exit. Never use the verb codes. Use only words a person can check by looking: its shape, material and place ("a stone arch on the left"), never a quality the eye cannot confirm ("a strong arch", "a safe bridge"). Its exit checks use the label word for word.
- **Each drawn exit has a `shape`.** `"object"` when the way out is a thing with edges that fits inside the frame: a doorway, an archway, an opening, a gap, a hole, a hatch, a grate, a bridge. `"path"` when it is a way that runs on out of the frame by nature: a road, a track, a streambed, a gully, a stream, a way between trees, a passage stretching away.
- **An `object` exit also has a `kind`**: the plain word for that kind of opening, a word of its `label` ("doorway", "grate", "arch", "hatch"). Exits of the same kind share the word.

## 2. Which images to make

- **Scenes.** One scene image, `img/<room>.jpg`. If a fixed object here has `states` and `changes` (the player can change its state, like a gate that opens), make one scene per state instead, `img/<room>.<STATE>.jpg`, in the order of `states`. A fixed object with `states` but no `changes` (like a ladder seen from above in one room and from below in another) only says which way it is seen from: one scene, showing the description that fits this room.
- **Items.** One item image per movable object, `items/<OBJECT>.png`. If the object has `states` whose descriptions look different, make one image per state instead, `items/<OBJECT>.<STATE>.png`, in the order of `states`. States with the same description share one image: make it once.
- **Edits.** Every image after the first of a group (the scenes of one room, or the state images of one item) is made as an edit of the first, so it stays the same picture with only the state changed. Set its `edit_of` to the first image's `file`. Otherwise `edit_of` is null.
- Nothing else.

## 3. What a picture contains

- **Everything the game's text says.** Every sentence of the description, and of the state's description, is drawn: what is there, and how things relate ("a stream flows out of the building": the water comes from the building).
- **Writing is drawn as writing.** When the text mentions words, draw them, in quotes: "the words \"MAGIC WORD XYZZY\" scratched into the mud".
- **No movable objects in scenes.** The game lays the item images over the scene. The scene must not contain them, or anything that could be mistaken for them: if there is a coin item, there are no coins, discs or medals in the background.
- **Setting.** The game is a medieval fantasy world. Describe every material as old and handmade: weathered stone, old brick laid by hand, rough timber, wrought iron. Concrete, machine-made bricks, sheet metal and anything modern do not belong. A thing the YAML names in modern words keeps its meaning in old materials: a "steel grate" is a grate of heavy hand-forged iron bars set in stone.
- **Dark rooms.** Draw the room by the light of the player's lamp: a single pool of warm light with darkness at the edges. Everything the description mentions must still be readable in the light. The lamp itself is not in the picture.
- **States.** All scenes of one room share the same camera, composition and lighting. Only the stateful object differs.
- **Stay faithful.** Draw only what the YAML states, plus these allowed choices:
  - what any picture needs: materials, layout, framing, light, and scenery seen through an exit that fits where it leads;
  - a visible detail that makes a YAML fact or state visible, such as frost on a window to show "freezing", or a lifted latch to show a cupboard "unlatched";
  - a concrete form for a thing the YAML names without saying what it looks like, how many there are, or what it is made of: "some fruit" drawn as three red apples, "a rope" as a coil of thick hemp rope. Put the YAML's word back in place of the drawn form: the sentence must still be true.
  A part attached to the named thing (the lid of a jar, the ring that holds a set of keys) is its form. A separate thing it rests in or on (a basket under the apples, a plate under the bread) is a new object and is not drawn, unless its only job is to show a YAML fact or state (icicles on a gutter for "freezing"). Scenery seen through an exit is the one place where things the YAML does not name may appear.

## 4. The picture decisions, and the prompt built from them

Every picture is described entirely by its `picture_decisions`: everything it shows, whether the YAML states it or you chose it. Each decision has:
- `why`, written first: where it comes from: the YAML sentence it draws ("YAML: 'A small stream flows out of the building and down a gully.'"), or the reason for a choice;
- `draw`, written after the `why`: one sentence for the image model, saying what is seen and where;
- `checks`: the questions that show it came out (section 6).

**Every sentence of the game's text is listed.** The game's text a picture must draw is: for the first scene, the room's `description.long`; for a scene made for a state, that state's description; for an item, its state's description, or its description. Each picture has a `sentences` list: every sentence of that text, word for word, with the numbers of the decisions that draw it (1 is the first decision listed): `{"sentence": "A small stream flows out of the building and down a gully.", "decisions": [3]}`. A sentence missing from the list, or drawn by no decision, is a sentence nobody drew: the script fails the spec.

**A sentence a rule changes is marked with that rule.** When one of this prompt's rules makes the picture differ from a sentence on purpose, add `"rule"` with the rule's name: `"setting"` (a "steel gangway" drawn in old timber), `"camera"` ("you are crawling" seen from standing eye height), `"compass"` (a hall "to the west" drawn on the left), `"items"` (in a scene, a movable object the sentence names, left out of it; in an item picture, a sentence that places the object somewhere, "There is a coil of rope on the deck here.", "A lantern hangs nearby.", since the item is drawn alone on a plain background). Example: `{"sentence": "The gangway is made of steel.", "decisions": [4], "rule": "setting"}`. The pipeline does not ask that sentence word for word, since a correct picture fails it; the checks of the decisions it points to verify it as drawn, so those decisions' checks must name what the rule makes of it ("Is the gangway made of old timber planks?"). Mark only a sentence a rule changes; every other sentence has no `rule`.

You do not write the prompt. The pipeline builds it from every `draw` sentence, in the order you list the decisions, then adds the shared style lines (frame, setting, style, mood; items have a style of their own). The prompt is cut off after about 250 words, so:
- **List the decisions in order of importance.** First what the picture is, in the camera's words: "A tall natural rock chamber, seen in a first-person view at standing eye height." Then what the game's text says matters most in this room, then each exit at its place, then the other details by place, the light last. For an item: the object first, then its state, then its details.
- **A thing with states: the thing, then its state, as separate decisions.** In the first picture of a group (section 2), one decision draws the thing as it always is, and its state shows in at most two details, each a decision of its own: "A tall clay jar with a wide wooden lid stands in the centre.", then "The jar is filled to the brim with grain." The edits of the group then replace only the state decisions, and everything else is checked as kept.
- All `draw` sentences together: 60 to 250 words of flowing prose that reads well when joined: full sentences, not a list of keywords. Present tense.
- **Name what is seen, and where:** foreground, left, right, centre. Each drawn exit at its compass place, in its exact words: "On the right, a narrow canyon mouth cuts into the rock." In a scene, every exit stays away from the edges of the frame.
- **Light by its source, direction and hardness:** "soft daylight from the sky above", "a single pool of warm lamplight from just behind the camera, the edges falling into darkness".
- **Concrete and literal.** Turn the description's images into what is seen: "orange stone in smooth wavy bands", not "frozen rivers of stone". No metaphors, no mood words, no quality tags ("detailed", "masterpiece").
- **Simple, common English.** Name a rare or old word by what is seen: "a well house" is "a small stone building built over a spring"; "a depression" is "a wide hollow in the ground". The image model and the judge read common words best.
- **Every sentence says what is there.** Never name a thing that must not be in the picture, not even to forbid it: the image model draws what it reads. "The wooden deck is bare", not "No coins or ropes on the deck"; "the shutters stand open", not "the bar is gone". The script rejects a `draw` with "no", "not", "without", "nothing" or "gone".
- **Draw results, not causes.** Name only what is in the final picture, never the tool or the action that made it: "the words \"MAGIC WORD XYZZY\" scratched into the mud", not "grooves scrawled with a stick".
- Do not mention the game, the player, "you" or the checks. Do not name an art style, a mood, the setting or the frame: the pipeline adds them.
- **A scene made for a state:** the camera, composition and lighting are the same as the room's other scenes; the object's state is described precisely.
- **An edit** (`edit_of` set) is drawn from the first picture by an editing model, which keeps everything the text does not name. Its decisions describe only what the new state changes. Their `draw` sentences, joined in order, are the whole text the editing model reads.
  - `replaces`: the numbers of the first picture's decisions this state replaces (1 is the first decision listed there): the decisions whose checks would be wrong on the edit. `[]` when it replaces none.
  - **One decision for each decision it replaces; an edit replaces at most two.** Its `draw` names the thing in the first picture's words and says what it is now: "Both wooden shutters now stand open, folded flat against the wall."
  - **A detail that showed the old state takes its new form, never left out.** "The wooden bar that held the shutters closed now stands upright beside the window."
  - **Do not describe what stays.** The pipeline copies the checks of the first picture's other decisions onto the edit, so the edit is also checked on what it keeps. Do not write them again.

## 5. How to write a check

Every check is a question about the picture that the judge answers with yes or no. The judge reads literally and sees nothing but the picture. A check works when a person who has never played the game, looking only at the picture, would answer it the same way you expect.
- **One thing.** One object or one property per check. Not "Is the tower stone with a red roof?" but two checks.
- **Name what is seen, and where.** "Is a rope bridge visible on the right side of the image?" Not "Can the player cross?".
- **Word every check in the positive: name a thing that could be in the picture.** Use `expected: false` for what must be absent: "Is a door visible in the image?", expected false. Never ask whether something is missing, hidden or cut off: not "Is there no door?", not "Is any part of the bridge cut off by the edge?", and never a double negative.
- **No vague words** that two people would read differently: "clearly", "nicely", "small", "appropriate", "realistic", and no graded words ("warm", "bright", "large", "old") unless compared with something in the picture. Use something that can be seen: "open", "on the left", "made of stone", "lit by a flame".
- **Only what the frame shows.** Nothing behind the camera, no sizes in numbers, nothing the eye cannot see (strength, weight, temperature, sound), no relation the eye cannot confirm ("the key fits the lock").
- **How a thing looks, not what it is made of**, unless the material can be seen: "Is the gate made of iron bars?" works; "Are the panes made of glass?" does not, since clear glass looks like nothing.
- **Nothing outside the picture**: not the YAML, not the game, not the prompt, not another check, not "the player", not "the way to the next room".
- **Absence checks list concrete kinds of thing**, at most four, that an image model is likely to add: "Is a ladder, staircase or rope visible?".
- **Simple, common English**, as in the draw sentences: a judge that does not know a word guesses. Not "Is the room a well house?" but "Is the water inside a stone building?".

## 6. The checks of each image

**Fixed checks.** The pipeline adds these to every image, so do not write them. Your decisions must still make each of them come true:
- every picture, first: it shows what the game's own text says (the room's description, the state's line, the object's description);
- every scene: the picture matches the room's description; a lit room is bright enough to see everywhere, a dark room is lit by a single pool of warm light with dark edges; no movable object of the room is in it; each fixed object without states is visible; a scene made for one state shows that state;
- every item: the object is visible and inside the picture, with nothing behind it (no floor, wall or landscape); a state image shows that state.

**The item's `label`.** These checks name a movable object by the `label` you give each item picture: the object as that picture shows it, in a few plain words, without "a" or "the": "little bird" for the bird out of its cage (its inventory name, "Little bird in cage", would ask a cageless picture about a cage), "shards of pottery" for the broken vase, "loaf of bread and wedge of cheese" for "Tasty food". A scene's checks that the object is absent use the label of its first item picture.
- every picture: nothing in it is out of its time or context for the medieval fantasy setting.

One fixed check is yours to write, because it is a judgment: for a fixed object with `states` but no `changes` (seen from one side here, from another elsewhere), add `Does the <name> fit this description: "<the description that fits this room>"?`, expected true, with `"origin": "required"`, in `checks`. `<name>` is the object's `inventory` name in lower case, without a leading `*`.

**Exit checks**, `"origin": "exit"`, every scene, in `checks`:
- For each drawn exit, that the thing the player taps is there, expected true: `Is <the label> visible in the image?`.
- For each drawn exit whose `shape` is `"object"`, a second check, expected true: `Is <the label> inside the picture?`. A `"path"` runs on out of the frame, so it gets only the first check.
- If the exit's `cond` is not met in this scene's state, the way is drawn but blocked. Then the first check asks about the blocking thing across the exit, naming the exit by its label word for word: "Is a shut wooden door across a stone archway on the left visible in the image?" for the label "a stone archway on the left".

**Decision checks.** Every picture decision carries its own checks: one or more questions that show it came out, one thing each. A decision with two visible parts gets two checks ("an iron gate with a chain" → the gate, the chain). No checks of your own outside the decisions; the pipeline copies them into `checks` with `"origin": "decision"`.
- A decision that adds something gets a check expecting true.
- **An absence check only when it earns its place:** for something this picture could plausibly gain and that no fixed check already covers, expected false, naming the concrete kinds an image model is likely to add: "Is a coin, rope or barrel visible on the deck?". A decision that leaves something bare needs no absence check of its own; give it one that shows what is there ("Is the deck made of bare wooden planks?").
- **One check per fact across the whole list.** Never ask again what a fixed or exit check already asks, in other words or the same: the script fails a spec that does. A decision that makes one of them visible is welcome, such as "a chain wrapped around the gate" for a locked gate.
- **A chosen form gets no check of its own.** A concrete form you chose for a thing the YAML names only in general (three apples for "some fruit") is checked through the thing itself ("Is some fruit visible?" is the fixed check's job); add a check for it only when it is how a game fact or state shows.
- **Every part of the game's sentence gets a check.** The fixed check quotes each sentence whole, and a judge answers it from a general impression. So split each sentence of `sentences` into its parts (each thing, place, material, relation or fact it states) and make sure the checks of the decisions it points to ask about every part, in simple words. "You are inside a building, a well house for a large spring." has three parts: inside a building ("Are walls and a roof visible around the viewer?"), a building made for the spring ("Is the water inside a stone building?"), a large spring ("Is water rising up out of the ground?"). A part a rule changes is checked as drawn (section 4).
- For an item made for one state, a decision tells it apart from its other states, such as "the sand is in the top half of the hourglass", with its checks.
