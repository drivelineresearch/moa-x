# Visual assets and fonts

MoA-X does not redistribute proprietary fonts or depend on a remote font
service. The Web UI prefers Gotham when it is already installed on the local
operating system, then falls back to a standard sans-serif stack. A clean
public clone therefore works without downloading or licensing typography
files, while a licensed local workstation can retain the intended Gotham
appearance.

The WebP/PNG illustrations under `harness/webui/static/images/` and
`harness/report/assets/` were generated specifically for this project and are
distributed with the repository under its MIT license. They are generic
provider- and workflow-themed characters and scenes, not official vendor
logos, licensed characters, or claims of endorsement by a model provider.

The animated provider files contain eight authored frames and are packaged as
looping animated WebP files. Their larger `*-work.webp` and
`*-victory.webp` companions are source sprite sheets retained so contributors
can resize or re-encode the animations without regenerating the art.

When contributing new assets:

- use work you created or have clear redistribution rights to;
- document third-party licenses and attribution in this file;
- strip embedded credentials, personal paths, comments, and unnecessary
  metadata before committing;
- prefer WebP/PNG/SVG files that can be served directly without a build step;
- include a static fallback and useful alternative text when the image conveys
  information;
- never commit screenshots containing private repositories, prompts, reports,
  account names, email addresses, API keys, or local file paths.
