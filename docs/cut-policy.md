# What Castwell should preserve and remove

The v9 policy candidate makes editorial boundaries explicit. It was tested and withheld after a commercial-boundary regression in the [paired pilot](policy-pilot-2026-10-09.md). The application retains v8. The rules below describe the experimental candidate, not the active classifier's exact instructions.

Model suggestions still require review: a policy describes the intended edit, not a guarantee that the detector finds it correctly. The model assigns roles to transcript units; it does not invent timestamps.

| Passage | Intended treatment |
| --- | --- |
| A sponsor's offer, endorsement, discount, terms or call to buy | Propose removal |
| A genuine paid read delivered as a joke | Propose removal, including the commercial joke |
| A persuasive anecdote or problem setup completed by the following offer | Include in that commercial read |
| A standalone greeting or speaker introduction with no offer or endorsement | Preserve, even immediately before an ad |
| A preview describing this episode or bonus coverage for existing premium members | Preserve when it does not ask listeners to buy, pay, join a paid tier or upgrade |
| An invitation to purchase membership, an event, merchandise or another host-owned product | Propose removal |
| A free request to follow the podcast, a production credit or a research citation | Preserve |
| An unpaid fictional sponsor sketch, quoted ad analysis or ordinary brand discussion | Preserve |
| A unit containing both meaningful editorial speech and a commercial pitch | Require boundary review |
| A relationship that cannot be resolved from the available context | Require review; do not invent a sponsorship |

These distinctions concern what is said and how surrounding speech establishes its role. A joke can be a real advertisement. A brand name can be ordinary conversation. Mentioning a premium episode can be program navigation, while asking a listener to purchase access is a sales pitch.

The greeting rule does not exempt a sponsor introduction or sponsor thanks. Likewise, preserving program navigation does not exempt a sales pitch simply because the host owns the product. A change back to editorial discussion ends a proposed commercial passage.

## Boundaries and confidence

Use the original transcript's word timing where available. A coarse segment containing both roles cannot support an invented sentence-level cut; it needs listening and adjustment. A separately identified editorial unit must not be absorbed merely because commercial speech occurs beside it.

The two classifier passes judge intent and edit boundaries. Agreement and confidence help organize review; they are not proof that a cut is correct. The .90 confidence threshold remains unchanged in the v9 experiment, and all actual cuts remain subject to the user's approval setting.

## Improving the references

Reference labels should use the same policy as the model instructions. When a policy changes, preserve the old labels and score both model versions against the revised labels. A better score caused only by relabeling a greeting is a policy change, not improved detection.

Before using examples for fine-tuning, verify both the spoken content and cut boundaries by listening, retain an uncertain category, and separate development from evaluation by episode and repeated ad script. The current recording references are provisional ASR and publisher annotations. They are useful for diagnostics but do not yet establish acoustic editing accuracy.

The exact candidate text is in [cut_policy.py](../castwell/cut_policy.py). The [earlier standardized comparison](standardized-model-comparison-2026-10-08.md) remains unchanged and records the previous policy's results.
