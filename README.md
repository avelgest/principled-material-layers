# Principled Material Layers
Principled Material Layers is a Blender add-on (for Blender 3.0+) that aims to 
provide a convenient way to layer and paint any materials that 
use the Principled BSDF node as their surface shader.

The add-on works by adding a *Material Layers* node to the Shader Editor to
which materials can be added and painted in Texture Paint mode. The Material
Layers node blends the channels (e.g. Base Color, Roughness etc.) of multiple
materials based on each layer’s painted alpha. Each enabled channel has a
corresponding output socket on the node, which often matches an input socket on
a Principled BSDF node, although any channel may be added.

**This add-on is currently in beta.**

![Suzanne painted with three materials](
https://user-images.githubusercontent.com/111190478/184520872-12deb2ec-1857-4e57-a20c-892b7e21e050.jpg)
*The Material Layers node with five enabled channels. Rust and rock materials*
*from Poly Haven.*

## Installation
Download the latest principled_material_layers ZIP file from the releases 
section, then from the Add-ons section of Blender’s preferences click 
*Install...* and select the downloaded .zip file. Enable the add-on labelled 
*“Material: Principled Material Layers”*.

## Features
- Use existing materials in the same blend file or directly from an asset
library (experimental) as layers.
- Freely edit or replace the materials in the layer stack at any time.
- Only requires adding a single node to a material’s node tree.
- Supports using any input socket from the Principled BSDF node as a channel,
and allows adding additional channels.
- Can also use a node group as a shader instead of the Principled BSDF node.
- Individually set the blend mode of each of a layer’s channels or disable the
channel entirely.
- Use node groups to mask layers or as custom blend modes.
- UDIM support (some features not available for UDIMs).

## Usage
See also the
[Getting Started](https://github.com/avelgest/principled-material-layers/wiki/Getting-Started)
guide and the
[documentation](https://github.com/avelgest/principled-material-layers/wiki).

Go into Texture Paint mode and open the *Material Layers* tab in the sidebar.
Press *Initialize*, adjust the settings in the pop-up and select which channels
to use, then press *OK*. A Material Layers node will be created and linked in the
active material’s node tree. Alternatively the node may be initialized from the
Shader Editor by selecting a Principled BSDF or Group node and continuing as
above.

After initialization the layer stack will be displayed in the
*Material Layers* tab. To paint a material select the layer in the layers list
when in Texture Paint mode, then paint using grayscale to modify the layer’s
alpha (white fully blends this layer’s material, black leaves the layer below
fully visible).

Layers can be added/removed using the '+' and '-' buttons next to layers list.
A layer’s material may be edited by pressing the node icon next to its name in
the layers list when there is an unpinned Shader Editor area open.
To load an existing material select a layer then press the *Replace Layer Material*
button, select a material then press *OK*. The layer will now contain a
copy of the material (note that the layer will not be affected by any subsequent
changes made to the original material).

A material asset can be loaded either by using the method above (for small
asset libraries) or by selecting an asset in an Asset Browser area and pressing
*Replace Layer Material* in the right-hand sidebar of the Asset Browser.

A layer’s channels may be added/removed or enabled/disabled in the *Active Layer*
panel. When a channel is disabled/removed from a layer then that layer
no longer contributes to the final value of the channel.

## Limitations
- Some features are not supported for UDIMs.
- Loading materials from an asset library outside of the asset browser area is
 considered experimental.
- Beta version. There may be bugs.
 
 https://user-images.githubusercontent.com/111190478/184711235-25bf5c51-ef9a-4372-a519-1eb4960c685a.mp4

## License
Principled Material Layer is release under the GNU General Public License,
version 2. See [LICENSE.txt](/LICENSE.txt) for details.
