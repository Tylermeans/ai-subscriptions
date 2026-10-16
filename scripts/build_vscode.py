#!/usr/bin/env python3
"""Build the dependency-free VS Code extension from an explicit file allowlist."""
import json
from pathlib import Path
import zipfile
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
FILES = ("package.json", "extension.js", "status.js", "media/dashboard.html", "README.md")


def build(root=ROOT):
    extension = root / "vscode"
    package = json.loads((extension / "package.json").read_text())
    manifest = ET.Element("PackageManifest", {"Version": "2.0.0",
        "xmlns": "http://schemas.microsoft.com/developer/vsx-schema/2011"})
    metadata = ET.SubElement(manifest, "Metadata")
    ET.SubElement(metadata, "Identity", {"Language": "en-US", "Id": package["name"],
        "Version": package["version"], "Publisher": package["publisher"]})
    ET.SubElement(metadata, "DisplayName").text = package["displayName"]
    ET.SubElement(metadata, "Description").text = package["description"]
    ET.SubElement(metadata, "Categories").text = "Other"
    properties = ET.SubElement(metadata, "Properties")
    for key, value in {"Microsoft.VisualStudio.Code.Engine": package["engines"]["vscode"],
                       "Microsoft.VisualStudio.Code.ExtensionKind": "ui",
                       "Microsoft.VisualStudio.Services.GitHubFlavoredMarkdown": "true"}.items():
        ET.SubElement(properties, "Property", {"Id": key, "Value": value})
    installation = ET.SubElement(manifest, "Installation")
    ET.SubElement(installation, "InstallationTarget", {"Id": "Microsoft.VisualStudio.Code"})
    ET.SubElement(manifest, "Dependencies")
    assets = ET.SubElement(manifest, "Assets")
    for kind, filename in (("Microsoft.VisualStudio.Code.Manifest", "package.json"),
                           ("Microsoft.VisualStudio.Services.Content.Details", "README.md")):
        ET.SubElement(assets, "Asset", {"Type": kind, "Path": "extension/" + filename, "Addressable": "true"})
    types = ET.Element("Types", {"xmlns": "http://schemas.openxmlformats.org/package/2006/content-types"})
    for suffix, mime in (("json", "application/json"), ("js", "application/javascript"),
                         ("html", "text/html"), ("md", "text/markdown"), ("vsixmanifest", "text/xml")):
        ET.SubElement(types, "Default", {"Extension": suffix, "ContentType": mime})
    output = root / "dist" / f'{package["name"]}-{package["version"]}.vsix'
    output.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in (("extension.vsixmanifest", ET.tostring(manifest, encoding="utf-8", xml_declaration=True)),
                              ("[Content_Types].xml", ET.tostring(types, encoding="utf-8", xml_declaration=True))):
            archive.writestr(name, content)
        for name in FILES:
            archive.write(extension / name, "extension/" + name)
    return output


if __name__ == "__main__":
    print(build())
