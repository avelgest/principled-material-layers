# Principled Material Layers

*For the latest documentation see 
[here](https://github.com/avelgest/principled-material-layers/wiki)*

Principled Material Layers is a system for layering and painting materials that
use the Principled BSDF shader node. It works by adding a new node (the
[Material Layers](#material-layers-node) node) to the shader editor that
represents a stack of material  layers with an output socket for each of the
stack’s enabled channels. The blending of the stack’s materials takes place
entirely inside the nodes internal node tree, so that the node itself can be
freely connected like any other node.

Any number of materials can be added to the stack and their node trees may be
modified at any time. Materials can be painted onto the active object by
selecting a material layer in the Texture Paint mode sidebar and painting as
usual.

## Interface
View3D > Texture Paint Sidebar > Material Layers Tab

or

Shader Editor > Sidebar > Material Layers Tab – When the material layer stack
is initialized

or

Shader Editor > Sidebar > Material Layers Tab – When the material layer stack
is uninitialized and a Principled BSDF, Group, or Material Output node is
active.

<img align="right"
src="https://user-images.githubusercontent.com/111190478/184524509-c374c5bf-fdfb-4563-a47b-5445a43c64b6.jpg"
alt="The Initialize pop-up"
/>

## Initialization
A material layer stack may be initialized from either the Shader Editor or from
Texture Paint mode when the selected object has a valid material.
From the Shader Editor select a Principled BSDF, a Node Group, or a Material
Output node (the selected node determines what channels the layer stack will
have) then open the Material Layers tab in the sidebar and press “Initialize”.
In Texture Paint mode the “Initialize” button is at the same position in the
sidebar, but behaves as if the default Material Output node were selected.
In the initialization pop-up select the size of image used for storing the
alpha value of the layers and which channels should initially be enabled.

If “Active Material as Base Layer” is checked then the base material of the
layer stack will be initialized from the active material. This will also enable
any channels that are present on the active material but not selected in the
pop-up.

## Usage
Initially the layer stack contains only the base layer which always fills the
entire area of the material and cannot be painted or removed. More layers can
be added using the "+" button to the right of the layer list at the top of the
Material Layers panel. Once a new layer is added the layer’s material may be
painted onto the mesh by going to Texture Paint mode and selecting the layer
in the layer list. When painting, white increases the alpha of the selected
layer whilst black decreases it. Note that the Erase/Add Alpha brush modes do
not modify the alpha of a material layer.

![The Material Painting panel](
https://user-images.githubusercontent.com/111190478/184524886-59b3ffa0-0812-4218-8e20-3596a90a5ef3.jpg)

<img align="right"
src="https://user-images.githubusercontent.com/111190478/184552314-adff8237-d101-4498-addb-b5312107e353.jpg"
alt="The Replace Layer Material pop-up"
/>

Existing materials can be added to the layer stack using “Replace Layer
Material”. This replaces the material of the active layer with a material from
the current Blender file or from an asset (currently experimental). What
channels the layer will have after its material is replaced is determined by
the "Channels" option in the pop-up, the options are: all the layer
stack's channels, only the enabled channels, only channels modified by the new
material, or channels that are either modified by the new material or enabled
on the layer stack.

When selecting from local materials in the “Replace Layer Material” the pop-up
only compatible materials (those which use a single surface shader with sockets
that match any of the layer stack’s channels) are displayed. For the asset view
all materials are displayed, though the compatibility of the selected asset can
be displayed if "Check Compatible" is enabled.

Material assets may also be loaded from an Asset Browser area by selecting the
asset, going to the right-hand sidebar of the Asset Browser, and pressing
"Replace Layer Material". As above, the compatibility of the selected asset can
be displayed in the sidebar if "Check Compatible" is enabled.

A layer’s shader node tree may be edited at any time by clicking the node icon
next to the layer’s name in the layer list or by selecting “Edit Nodes” in the
Material Layers panel. These will open the node tree as a node group in an
available Shader Editor area (i.e. a Shader Editor without a pinned node tree),
which can be closed (default ‘Tab’) like any other node group.

Simple changes to the active layer’s node tree can be made by selecting the
channel to modify in the Active Layer panel and using the socket buttons at the
bottom of the panel.

The final alpha value of a layer is the product of three values: its opacity,
which can be changed using the slider beneath the layers list; its painted
value (not available for the base layer), which is modified by painting in
Texture paint mode; and optionally its node mask, a node group that can be
added in the Active Layer sub-panel (see [Node Mask](#node-mask) for details).

<img align="right"
src="https://user-images.githubusercontent.com/111190478/184525000-008b9a12-8be6-4d5b-b868-e9dd942e0a30.jpg"
alt="The Active Layer sub-panel"
/>

## Channels
The layer stack initially contains a number of channels each of which
corresponds with an input socket on a Principled BSDF or Material Output node
(if a group node was selected during initialization then its sockets will be
used instead of the Principled BSDF node’s). Channels can be enabled/disabled
in the Layer Stack Channels panel; disabled channels will not appear as outputs
on the material layers node.

Each layer may have some or all of the layer stack’s channels, and channels can
be added or removed from the layer using the “+” and “-” buttons under the
channels list in the Active Layer panel. The base layer always has all of the
layer stack’s enabled channels.

## Blending
The blend mode of each of a layer’s channels can be changed by selecting that
layer and using the drop-down menus in the channels list of the Active Layer
panel. Note that the menus are not displayed for the base layer since it does
not require blending.

The "Custom” blend mode allows a node group to be used as a custom blending
operation. The node group may be set by selecting the channel in the active
layer's channels list and using the "Custom Blend Mode" menu below the list.
Node groups used as custom blending modes should have inputs and outputs like a
MixRGB shader node and behave similarly (blend two color inputs using a scalar
factor to produce one color output).

## Baking
Baking in this add-on is mainly intended as a way to potentially improve
performance when using computationally expensive materials as layers.
Baking textures for export etc. should be performed as usual using the normal
Bake panel.

Layers can be baked by pressing the “B” icon near the layer’s name in the
layers list; pressing the icon again will free the bake. The settings used for
baking are found in the Settings sub-panel of the Material Layers tab. Whilst
baked any changes made to the layer’s node tree will not be visible until the
bake is freed. Changes to the layer’s opacity, node mask, and channel blending
modes will still work as normal however.

The entire layer stack can be baked using the “Bake Layer Stack” operator in
the Material Painting panel. This bakes all the enabled channels in the stack
to images. Whilst baked any changes to the layer stack will not be visible
until the bake is freed. If the “Hide Images” checkbox is unticked when baking
then the created images will have names of the form “{material name}
{channel name} baked”, note that these images will be deleted when the bake is
freed or the file is reloaded unless they are first saved to disk.

## Node Mask
Each layer may optionally have a node mask. This is a node group with a single
scalar output which is multiplied with the layer’s opacity and painted alpha
value to calculate the layer’s final alpha value. A node mask may be added
through the Active Layer panel. The “Apply Node Mask” button permanently
multiplies the layer's painted alpha value by the node mask.


<img align="right"
src="https://user-images.githubusercontent.com/111190478/184549707-2ba1401c-64a5-4df0-bf63-39c3922e54da.jpg"
alt="The Material Layers node"
/>


## Material Layers Node
This node outputs the final value of each of the layer stack’s channels and has
one output socket for each enabled channel. The node can mostly be used just
like any other node and may be safely moved, relinked or deleted, however,
placing the node in a node group is not supported. Additional Material Layers
nodes may be added from the usual Add menu if the active material has an
initialized layer stack. Note that these node will all represent the same
layer stack.


## Troubleshooting
Several tools to help diagnose and fix problems in the add-on can be found in
the Debug sub-panel of the Material Layers tab of the sidebar.

The layer stack’s internal node tree can be viewed in an open Shader Editor by
pressing “View Stack Node Tree”. Note that since the internal node tree is
frequently rebuilt any changes made to it are likely to be lost.

When a Material Layers node is active with another node selected the
“Link Sockets by Name” operator can be used to quickly connect all the outputs
of the Material Layers node to any input sockets with the same name on the
selected node.


### Sockets stop updating / changing blend modes has no effect etc.
This is probably due to msgbus RNA subscriptions becoming invalid, which may
be fixed by pressing “Msgbus Resubscribe” in the Debug sub-panel.

### Layer is blank after undo/redo in Texture Paint mode
If this happens then it’s best to not make any changes or change the active
layer. This can often be fixed by undoing a couple of times then redoing a
couple of times. The option “Use Undo Workaround” in the add-on preferences may
help prevent this from happening.

### Long pause when changing layer
This can occur when using very large layers, since by default a layer’s image
data is packed into the same image as other layers to save memory. Thus the
image data must be extracted before it can be used, which may cause a delay
when using large images. Checking “Use NumPy” in the add-on preferences may
speed this up. This behavior can also be disabled entirely by unticking the
“Layers Share Images” option, though this will increase memory usage and won’t
affect existing layer stacks.

### Long pauses when generating layer previews
Layer previews can be disabled by clicking the triangular button in the bottom
left of the layer list and unticking “Show Previews” or equivalently by
unticking “Show Layer Material Previews” in the add-on preferences.
Alternatively changing the render engine to Cycles may help in some cases.

### Crash after undo with “Show Previews” enabled
Undoing a global undo step (this includes things like changing the active layer
or adding/removing layers, but does not include making a brush stroke in Texture
Paint mode) whilst a layer preview is being updated may cause a crash.
Currently the only solution if you experience this issue is to disable layer
previews.

### Crash after "Replace Layer Material"
When pressing "OK" in the "Replace Layer Material" pop-up causes a crash, this
may be due to the method used by the add-on when copying materials. Unchecking
"Use Op-Based Material Copy" in the add-on preferences may solve this.
