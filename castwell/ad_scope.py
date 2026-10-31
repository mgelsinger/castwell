"""User-selected removal scope, shared by all contextual classifiers."""

REMOVAL_SCOPE = "sponsor-product-and-podcast-promos-v2"

INSTRUCTIONS = """
Removal scope: sponsor/product reads AND distinct genuine promos or trailers for OTHER podcasts,
including free podcasts. Label those genuine podcast promotions commercial even when
no payment, purchase, or sponsorship is established. Include their persuasive setup,
story premises, cast descriptions, schedules, and invitations to listen or follow.
Generic network CATALOGUE invitations are also podcast promotions, even without
naming a particular show: "find more podcasts", "discover our other shows", or
"for more podcasts, visit our app or website". Include the associated app/platform
call to action when it promotes discovering or listening to the network's other podcasts.
Neutral production attribution alone, such as "a production of Example Network",
stays editorial. Split that credit from an adjacent catalogue invitation; a shared
publisher name does not make the invitation a neutral credit. An ordinary direction
to follow or listen to ONLY the CURRENT show stays editorial, including its app location.
Keep short contextual podcast recommendations woven into the current conversation,
including a topical invitation to "check out" or "listen to" another show's related
episode. A direct listening invitation alone does not make that conversational aside
a distinct promotional passage. Preserve its connection to the surrounding topic and
the speakers' responses, as well as incidental references to earlier coverage.
Distinct promotional passages, trailers and network catalogue invitations remain
commercial: their purpose is promoting discovery of a show rather than continuing
the current discussion. Judge the passage in context, not by a listening verb alone.
Actual sponsor, product, affiliate, paid-service and paid-subscription offers remain
commercial, including the host's own paid offer, persuasive setup, jokes and terms.
Embedding an actual paid, affiliate or sponsor offer in conversation does not protect it.
An unsolicited sample or listener gift, tasting, praise, thanks, or unsponsored shoutout
alone is independent product discussion, not a sponsor relationship or sales pitch.
Look for an actual offer, persuasive sales conversion, or paid/affiliate/obligated
arrangement in context. A disclaimer such as "not sponsored" is not a blanket exemption:
an actual sales pitch or genuine other-podcast promotion still belongs in the removal scope.
Humor does not protect a genuine commercial read or genuine podcast promotion.
Preserve fake ads, fictional sponsor sketches, unpaid parody/jokes, quoted ads being
discussed as editorial, ordinary discussion, and independent product or podcast discussion.
A fictional podcast invented for a joke is not a genuine podcast promotion.
Preserve the CURRENT show's ordinary navigation, episode previews, free follow requests,
and neutral publisher or production credits. A current-show paid offer remains commercial.
A short connector or return bridge belongs with the clause it introduces. Preserve
"and" or similar words when they resume show discussion, even if ASR isolates the word
in a separate unit. A connector continuing a real promotion remains commercial.
Split actual promotions from neighboring editorial speech. Two target promotion types
in the same unit do not make it mixed. If one indivisible unit contains both target
promotion and meaningful protected speech, mark mixed or uncertain and require review.
Insertion between ads, professional delivery, a brand, or a URL alone does not establish
genuine promotional intent. Use the full framing before and after performed or quoted
speech. Do not invent payment or an actual sales relationship; payment is not required
to remove a genuine promo for another podcast.
"""
