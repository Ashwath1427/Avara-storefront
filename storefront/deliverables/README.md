# Private AVARA release files

Do not commit customer release ZIPs or the full AVARA source to the public storefront repository.

The approval service expects these private files by default:

- `avara-basic.zip`
- `avara-pro.zip`

On Free Render, upload them to a private Supabase Storage bucket named `avara-releases`. On paid Render, you can instead keep them on a private persistent disk and set `AVARA_BASIC_ZIP` and `AVARA_PRO_ZIP` to their absolute paths. The owner screen refuses approval when the matching ZIP is missing. Downloads require a signed-in customer account with an approved order for that plan.
