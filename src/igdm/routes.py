"""Built-in reply routes, offered to Jev next to the prewritten bank."""

BUILTINS: dict[str, str] = {
    "decline": "They no longer want it.",
    "email_elsewhere": "Their email is in their bio or somewhere else, or they want it sent "
                       "another way, but they haven't typed an email address here.",
    "off_topic": "Not about the material: a business pitch, a question about another reel, "
                 "or something personal.",
    "upset": "A complaint, annoyance or anger.",
    "custom": "About the material, but none of the other options fit.",
}
