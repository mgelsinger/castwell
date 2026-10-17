"""User-selected removal scope, shared by all contextual classifiers."""

REMOVAL_SCOPE = "sponsor-product-and-podcast-promos-v2"

INSTRUCTIONS = """
Removal scope: sponsor/product reads AND genuine promos or trailers for OTHER podcasts,
including free podcasts. Label those genuine podcast promotions commercial even when
no payment, purchase, or sponsorship is established. Include their persuasive setup,
story premises, cast descriptions, schedules, and invitations to listen or follow.
Actual sponsor, product, affiliate, paid-service and paid-subscription offers remain
commercial, including the host's own paid offer, persuasive setup, jokes and terms.
Humor does not protect a genuine commercial read or genuine podcast promotion.
Preserve fake ads, fictional sponsor sketches, unpaid parody/jokes, quoted ads being
discussed as editorial, ordinary discussion, and independent product or podcast discussion.
A fictional podcast invented for a joke is not a genuine podcast promotion.
Preserve the CURRENT show's ordinary navigation, episode previews, free follow requests,
and neutral publisher or production credits. A current-show paid offer remains commercial.
Split actual promotions from neighboring editorial speech. Two target promotion types
in the same unit do not make it mixed. If one indivisible unit contains both target
promotion and meaningful protected speech, mark mixed or uncertain and require review.
Insertion between ads, professional delivery, a brand, or a URL alone does not establish
genuine promotional intent. Use the full framing before and after performed or quoted
speech. Do not invent payment or an actual sales relationship; payment is not required
to remove a genuine promo for another podcast.
"""
