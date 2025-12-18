import adsk.core
import adsk.fusion
import traceback
import json
import os
import threading
from .folder_walker import FolderWalkingThread
from .folder_walker import get_cots_files
from .folder_walker import myCustomEvent

from .lib import fusionAddInUtils as futil


# Keep a global reference to event handlers so they are not garbage collected.
handlers = []

# Custom event to signal the folder walker thread has finished and the
# palette needs to be updated and enabled
customEvent = None

# Global state
g_favorites = {}       # dataFile.id -> bool
g_palette = None       # HTML palette reference
g_walker_thread_running = False

app = adsk.core.Application.get()
ui = app.userInterface


def get_app_ui():
    app = adsk.core.Application.get()
    ui = app.userInterface if app else None
    return app, ui


def _favorites_path():
    """Path to the favorites JSON file next to this add-in."""
    folder = os.path.dirname(__file__)
    return os.path.join(folder, 'FRC_COTS_favorites.json')

def load_favorites():
    """Load favorites mapping from disk."""
    global g_favorites

    futil.log( f'Loading favorites...')

    try:
        path = _favorites_path()
        if os.path.exists(path):
            with open(path, 'r') as f:
                g_favorites = json.load(f)
        else:
            g_favorites = {}
    except Exception:
        g_favorites = {}


def save_favorites():
    """Persist favorites mapping to disk."""
    try:
        path = _favorites_path()
        with open(path, 'w') as f:
            json.dump(g_favorites, f, indent=2)
    except Exception:
        pass

def insert_part_at_targets(design: adsk.fusion.Design, label, data_file, targets, ui):
    """
    Insert the given DataFile into the root component and create joints
    from its origin to each of the target entities.
    """
    root_comp = design.rootComponent
    occs = root_comp.occurrences
    joints = root_comp.joints

    for target_entity in targets:
        # Insert as reference occurrence at identity transform
        transform = adsk.core.Matrix3D.create()
        new_occ = occs.addByInsert(
            data_file,
            transform,
            True  # reference to original design
        )

        # Ensure the inserted occurrence is not grounded so joints can move it
        try:
            new_occ.isGroundToParent = False
        except:
            # If this property is not available for any reason, ignore and continue
            pass

        comp = new_occ.component
        origin_native = comp.originConstructionPoint
        if not origin_native:
            ui.messageBox(
                "Inserted component '{}' has no originConstructionPoint.".format(label)
            )
            continue

        origin_proxy = origin_native.createForAssemblyContext(new_occ)
        if not origin_proxy:
            ui.messageBox(
                "Failed to create origin proxy for '{}'.".format(label)
            )
            continue

        joint_geo_cots = adsk.fusion.JointGeometry.createByPoint(origin_proxy)

        # Build target joint geometry based on the selected entity type
        joint_geo_target = None

        if isinstance(target_entity, adsk.fusion.BRepEdge):
            joint_geo_target = adsk.fusion.JointGeometry.createByCurve(
                target_entity,
                adsk.fusion.JointKeyPointTypes.CenterKeyPoint
            )

        elif isinstance(target_entity, adsk.fusion.BRepFace):
            face = target_entity
            surf = face.geometry

            if isinstance(surf, adsk.core.Plane):
                joint_geo_target = adsk.fusion.JointGeometry.createByPlanarFace(
                    face,
                    None,
                    adsk.fusion.JointKeyPointTypes.CenterKeyPoint
                )
            else:
                joint_geo_target = adsk.fusion.JointGeometry.createByNonPlanarFace(
                    face,
                    None
                )

        elif isinstance(target_entity, adsk.fusion.JointOrigin):
            joint_geo_target = adsk.fusion.JointGeometry.createByJointOrigin(
                target_entity
            )

        else:
            ui.messageBox(
                "Unsupported selection type for joint target: {}".format(
                    type(target_entity)
                )
            )
            continue

        if joint_geo_target is not None:
            joint_input = joints.createInput(
                joint_geo_cots,
                joint_geo_target
            )
            joint_input.setAsRigidJointMotion()

            # Flip the default joint orientation (for example, 180 degrees about its primary axis)
            try:
                joint_input.isFlipped = True
            except:
                # If this property is not available, just ignore and proceed
                pass

            joints.add(joint_input)


def _palette_html_path():
    """Path to the HTML palette file."""
    return os.path.join(os.path.dirname(__file__), 'frc_cots_palette.html')

def load_palette():
    """Load the HTML palette used to browse COTS parts."""
    global g_walker_thread_running

    futil.log(f'load_palette()....')

    try:
        palette = get_or_create_palette(ui)
        if palette:
            try:
                palette.isVisible = True
            except:
                # If Fusion is unhappy about toggling visibility, just ignore it.
                pass

            parts = []
            cots_files = get_cots_files()
            for idx, (path, label, df, icon_name) in enumerate(cots_files):
                parts.append({
                    'index': idx,
                    'path': path,
                    'label': label,
                    'favorite': g_favorites.get(df.id, False),
                    'thumb': icon_name,
                    'loading': g_walker_thread_running
                })
            futil.log(f'   Sending {len(parts)} records to palette...')
            palette.sendInfoToHTML('partsList', json.dumps(parts))
    except:
        futil.handle_error('load_palette failed:')


def get_or_create_palette(ui: adsk.core.UserInterface) -> adsk.core.Palette:
    """Create or return the HTML palette used to browse COTS parts."""
    global g_palette

    futil.log(f'get_or_create_palette()....')

    pal_id = 'FRC_COTS_Palette'
    # If we already have a valid reference, reuse it
    if g_palette and g_palette.isValid:
        return g_palette

    pal: adsk.core.Palette = ui.palettes.itemById(pal_id)
    if pal and not pal.isValid:
        # Stale palette, remove so we can recreate
        pal.deleteMe()
        pal = None

    if not pal:
        html_path = _palette_html_path()
        url = 'file:///' + html_path.replace('\\', '/')
        pal = ui.palettes.add(
            pal_id,
            'FRC COTS Library',
            url,
            True,   # isVisible
            True,   # showCloseButton
            True,   # isResizable
            350,
            600
        )

        html_handler = FRCHTMLHandler()
        pal.incomingFromHTML.add(html_handler)
        handlers.append(html_handler)

    g_palette = pal
    return pal

class FolderWalkerThreadDoneEventHandler(adsk.core.CustomEventHandler):
    def __init__(self):
        super().__init__()
    def notify(self, args):
        global g_walker_thread_running
        g_walker_thread_running = False
        load_palette()

class FRCHTMLHandler(adsk.core.HTMLEventHandler):
    """Handles messages coming from the HTML palette."""

    def notify(self, args):

        futil.log(f'FRCHTMLHandler::notify()...')

        try:
            action = args.action
            data = args.data or ''

            # HTML asks for the list of parts
            if action == 'requestParts':
                load_palette()
                return

            # HTML tells us to insert the selected part at current canvas selection
            if action == 'insertPart':
                try:
                    payload = json.loads(data) if data else {}
                    idx = int(payload.get('index', -1))
                except Exception:
                    idx = -1

                cots_files = get_cots_files()
                if idx < 0 or idx >= len(cots_files):
                    ui.messageBox('Invalid part index from HTML.')
                    return

                path, label, data_file, _ = cots_files[idx]
                project = os.path.join( path, label )

                # Get current canvas selections as targets
                sels = ui.activeSelections
                targets = [sels.item(i).entity for i in range(sels.count)]
                if not targets:
                    ui.messageBox(
                        'No geometry selected.\n\n'
                        'Select one or more faces, edges, or joint origins in the canvas, '
                        'then click the part in the FRC COTS palette.'
                    )
                    return

                design = adsk.fusion.Design.cast(app.activeProduct)
                if not design:
                    ui.messageBox('No active Fusion design.')
                    return

                insert_part_at_targets(design, project, data_file, targets, ui)
                return

            # HTML toggles favorite state for a part
            if action == 'toggleFavorite':
                try:
                    payload = json.loads(data) if data else {}
                    idx = int(payload.get('index', -1))
                    fav = bool(payload.get('favorite', False))
                except Exception:
                    idx = -1
                    fav = False

                cots_files = get_cots_files()
                if 0 <= idx < len(cots_files):
                    _, _label, df, _thumbidx = cots_files[idx]
                    g_favorites[df.id] = fav
                    save_favorites()
                return

        except:
            ui.messageBox('HTML palette error:\n{}'.format(traceback.format_exc()))


def run(context):
    global g_walker_thread_running
    try:

        cmd_id = 'FRC_InsertCOTS'
        cmd_def = ui.commandDefinitions.itemById(cmd_id)
        if not cmd_def:
            cmd_def = ui.commandDefinitions.addButtonDefinition(
                cmd_id,
                'FRC COTS Library',
                'Open the FRC COTS library palette to insert components'
            )

        global customEvent
        customEvent = app.registerCustomEvent(myCustomEvent)
        onThreadEvent = FolderWalkerThreadDoneEventHandler()
        customEvent.add(onThreadEvent)
        handlers.append(onThreadEvent)

        class ShowPaletteCreatedHandler(adsk.core.CommandCreatedEventHandler):
            def __init__(self):
                super().__init__()

            def notify(self, args):

                futil.log(f'ShowPaletteCreatedHandler::notify()...')

                load_palette()

        on_created = ShowPaletteCreatedHandler()
        cmd_def.commandCreated.add(on_created)
        handlers.append(on_created)

        # Put the button on the Insert panel in the Design workspace
        solid_ws = ui.workspaces.itemById('FusionSolidEnvironment')
        panels = solid_ws.toolbarPanels
        insert_panel = panels.itemById('InsertPanel')
        if insert_panel:
            control = insert_panel.controls.itemById(cmd_id)
            if not control:
                insert_panel.controls.addCommand(cmd_def, '')

        # Load favorites and COTS list once when the add-in starts
        load_favorites()
        # load_cots_files(app)

        stopFlag = threading.Event()
        folderWalker = FolderWalkingThread(stopFlag)
        folderWalker.start()
        g_walker_thread_running = True

        load_palette()

    except:
        ui.messageBox('Add-in run failed:\n{}'.format(traceback.format_exc()))


def stop(context):
    app, ui = get_app_ui()
    try:
        cmd_id = 'FRC_InsertCOTS'

        # Remove the toolbar button
        solid_ws = ui.workspaces.itemById('FusionSolidEnvironment')
        panels = solid_ws.toolbarPanels
        insert_panel = panels.itemById('InsertPanel')
        if insert_panel:
            control = insert_panel.controls.itemById(cmd_id)
            if control:
                control.deleteMe()

        # Remove the command definition
        cmd_def = ui.commandDefinitions.itemById(cmd_id)
        if cmd_def:
            cmd_def.deleteMe()

        # Remove the HTML palette if it exists
        global g_palette
        if g_palette:
            g_palette.deleteMe()
            g_palette = None
        else:
            pal = ui.palettes.itemById('FRC_COTS_Palette')
            if pal:
                pal.deleteMe()

    except:
        ui.messageBox('Add-in stop failed:\n{}'.format(traceback.format_exc())) 