# Principled Material Layers
Principled Material Layers is a Blender add-on (for Blender 3.1+) that aims to 
provide a convenient and performant way to layer and paint any materials that 
use the Principled BSDF node as their surface shader.

The add-on works by adding a “Material Layers” node to the Shader Editor to
which materials can be added and painted in Texture Paint mode. The Material
Layers node blends the channels (e.g. Base Color, Roughness etc.) of multiple
materials based on each layer’s painted alpha. Each enabled channel has a
corresponding output socket on the node, which often matches an input socket on
a Principled BSDF node, although any channel may be added.

**This add-on is currently in early-beta and has not been thoroughly tested.**
**Some bugs are to be expected.**

![Suzanne painted with three materials](
https://user-images.githubusercontent.com/111190478/184520872-12deb2ec-1857-4e57-a20c-892b7e21e050.jpg)
*The Material Layers node with five enabled channels. Rust and rock materials*
*from Poly Haven.*

## Installation
Download the latest principled_material_layers ZIP file from the releases 
section, then from the Add-ons section of Blender’s preferences click 
“Install...” and select the downloaded .zip file. Enable the add-on labelled 
“Material: Principled Material Layers”.

## Features
- Use existing materials in the same blend file or directly from an asset
library (experimental) as layers.
- Freely edit or replace the materials in the layer stack at any time.
- Only requires adding a single node to a material’s node tree.
- Supports using any input socket from the Principled BSDF node as a channel,
and allows adding additional channels.
- Individually set the blend mode of each of a layer’s channels or disable the
channel entirely.
- Use a node group as a custom blend mode.
- Add node-based masks to layers in the layer stack.

## Usage
Go into Texture Paint mode and open the “Material Layers” tab in the sidebar.
Press “Initialize”, adjust the settings in the pop-up and select which channels
to use, then press OK. A Material Layers node will be created and linked in the
active material’s node tree. Alternatively the node may be initialized from the
Shader Editor by selecting a Principled BSDF or Group node and continuing as
above.

After initialization the layer stack will be displayed in the
“Material Layers” tab. To paint a material select the layer in the layers list
when in Texture Paint mode, then paint using grayscale to modify the layer’s
alpha (white fully blends this layer’s material, black leaves the layer below
fully visible).

Layers can be added/removed using the '+' and '-' buttons next to layers list.
A layer’s material may be edited by pressing the node icon next to its name in
the layers list when there is an unpinned Shader Editor area open.
To load an existing material select a layer then press the “Replace Layer
Material” button, select a material then press OK. The layer will now contain a
copy of the material (note that the layer will not be affected by any subsequent
changes made to the original material).

A layer’s channels may be added/removed or enabled/disabled in the “Active
Layer” panel. When a channel is disabled/removed from a layer then that layer
no longer contributes to the final value of the channel.

For more information see the [documentation](/docs.md).

## Limitations
- UDIMs are not yet supported.
- Loading materials from an asset library is considered experimental.
- Early beta version, has not been thoroughly tested (see the issues section of
 the repository for known bugs).

## License
Principled Material Layer is release under the GNU General Public License,
version 2. See [LICENSE.txt](/LICENSE.txt) for details.
