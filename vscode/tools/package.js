#!/usr/bin/env node
'use strict';
// Builds qrepo-VERSION.vsix without vsce: a VSIX is a zip of the extension
// under extension/, a manifest and a content-types file.  Only what the
// extension runs is packed: package.json, extension.js, lib/, README.md.
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');

const here = path.resolve(__dirname, '..');
const pkg = JSON.parse(fs.readFileSync(path.join(here, 'package.json'), 'utf8'));
const out = path.join(here, `${pkg.name}-${pkg.version}.vsix`);
const stage = fs.mkdtempSync(path.join(os.tmpdir(), 'qrepo-vsix-'));
const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

fs.mkdirSync(path.join(stage, 'extension'));
for (const f of ['package.json', 'extension.js', 'README.md']) fs.copyFileSync(path.join(here, f), path.join(stage, 'extension', f));
fs.cpSync(path.join(here, 'lib'), path.join(stage, 'extension', 'lib'), { recursive: true });
fs.writeFileSync(path.join(stage, '[Content_Types].xml'),
  '<?xml version="1.0" encoding="utf-8"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">' +
  '<Default Extension=".json" ContentType="application/json"/><Default Extension=".js" ContentType="application/javascript"/>' +
  '<Default Extension=".md" ContentType="text/markdown"/><Default Extension=".vsixmanifest" ContentType="text/xml"/></Types>\n');
fs.writeFileSync(path.join(stage, 'extension.vsixmanifest'), `<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011" xmlns:d="http://schemas.microsoft.com/developer/vsx-schema-design/2011">
  <Metadata>
    <Identity Language="en-US" Id="${esc(pkg.name)}" Version="${esc(pkg.version)}" Publisher="${esc(pkg.publisher)}" />
    <DisplayName>${esc(pkg.displayName)}</DisplayName>
    <Description xml:space="preserve">${esc(pkg.description)}</Description>
    <Tags></Tags>
    <Categories>${esc(pkg.categories.join(','))}</Categories>
    <GalleryFlags>Public</GalleryFlags>
    <Properties>
      <Property Id="Microsoft.VisualStudio.Code.Engine" Value="${esc(pkg.engines.vscode)}" />
      <Property Id="Microsoft.VisualStudio.Code.ExtensionDependencies" Value="" />
      <Property Id="Microsoft.VisualStudio.Code.ExtensionPack" Value="" />
      <Property Id="Microsoft.VisualStudio.Code.ExtensionKind" Value="workspace" />
      <Property Id="Microsoft.VisualStudio.Code.LocalizedLanguages" Value="" />
    </Properties>
  </Metadata>
  <Installation><InstallationTarget Id="Microsoft.VisualStudio.Code"/></Installation>
  <Dependencies/>
  <Assets>
    <Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" Addressable="true" />
    <Asset Type="Microsoft.VisualStudio.Services.Content.Details" Path="extension/README.md" Addressable="true" />
  </Assets>
</PackageManifest>
`);
fs.rmSync(out, { force: true });
execFileSync('zip', ['-q', '-r', '-X', out, '[Content_Types].xml', 'extension.vsixmanifest', 'extension'], { cwd: stage });
fs.rmSync(stage, { recursive: true, force: true });
console.log(out);
