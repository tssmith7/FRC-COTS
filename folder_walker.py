import adsk.core
import os
import threading
import heapq

from .lib import fusionAddInUtils as futil


myCustomEvent = 'FolderWalkerDoneEvent'


# Global state
g_cots_files = []      # list of (path, partname, DataFile, icon_name)
g_cots_lock = threading.Lock()
g_thumbnail_futures = []      # list of (icon_name, future)

app = adsk.core.Application.get()
ui = app.userInterface

def get_cots_files():
    g_cots_lock.acquire()
    cots_files = g_cots_files
    g_cots_lock.release()
    return cots_files

def get_icon_filename( path:str, basename:str ):
    """Path to the favorites JSON file next to this add-in."""
    safeName = basename.replace("\\", "")
    safeName = safeName.replace(" ", "")
    safeName = safeName.replace("[", "")
    safeName = safeName.replace("]", "")
    safeName = safeName.replace("^", "")
    safeName = safeName.replace("+", "")
    safeName = safeName.replace("$", "")
    safeName = safeName.replace("%", "")
    safeName = safeName.replace( "/", "_")
    safeName = safeName.replace( ".", "_")
    safeName = safeName.replace( "\"", "in")
    path = path.replace( '\\', '_' )
    path = path.replace( '/', '_' )
    folder = os.path.dirname(__file__)
    return os.path.join(folder, 'icons', f'{path}{safeName}.png')

def _walk_folder(folder: adsk.core.DataFolder, prefix, out_list):
    """Recursively walk a DataFolder and append (label, DataFile) for .f3d files."""
    global g_cots_files
    global g_cots_lock
    global g_thumbnail_futures

    # Files
    # progress.message = f'Loading folder {prefix}, file %v ...'
    idx = 0
    for df in folder.dataFiles:
        # if progress.wasCancelled:
        #     out_list = []
        #     return
        
        try:
            adsk.doEvents()
            if df.fileExtension and df.fileExtension.lower() == 'f3d':
                label = df.name
                icon_name = get_icon_filename(prefix,label)
                if g_cots_lock.acquire( True, 0.5):
                    heapq.heappush( out_list, (prefix, label, df, icon_name))
                    g_cots_lock.release()
                else:
                    futil.log( 'Cannot lock the g_cots_files list...')
                g_thumbnail_futures.append((icon_name, df.thumbnail))
                # futil.log(f'Loading file {label} at idx {len(out_list)}...')
                idx = idx + 1
                # progress.progressValue = idx
        except:
            futil.log(f'Failed to load file {df.name}.')
            continue
    
    futil.log( f'Folder {folder.name}, loaded {idx} files...')

    # Subfolders
    for sub in folder.dataFolders:
        new_prefix = prefix + sub.name + '/'
        _walk_folder(sub, new_prefix, out_list)


def load_cots_files():
    """Populate g_cots_files with all .f3d files under the FRC_COTS project."""
    global g_cots_files

    g_cots_files = []

    futil.log(f'Loading COTS files....')

    # progress = ui.createProgressDialog()
    try:
        data = app.data
        projects = data.dataProjects

        frc_project = None
        for proj in projects:
            if proj.name == 'FRC_COTS':
                frc_project = proj
                break

        if not frc_project:
            futil.popup_error( "Could not find project FRC_COTS.")
            return

        root = frc_project.rootFolder

        # progress.show( "Loading COTS files from FRC_COTS Project...", "", 0, 20 )
        # _walk_folder(progress, root, '', g_cots_files)
        _walk_folder(root, '', g_cots_files)
        futil.log( f'Loaded {len(g_cots_files)} COTS files...')

        # Sort nicely by label
        # g_cots_files.sort(key=lambda t: (t[0] + t[1]).lower())
    except:
        futil.handle_error( "Load COTS files" )
        g_cots_files = []
    # progress.hide()


class FolderWalkingThread(threading.Thread):
    def __init__(self, event):
        threading.Thread.__init__(self)
        self.stopped = event

    def run(self):
        global g_cots_files
        global g_thumbnail_futures

        futil.log(f'FolderWalkingThread::run()...')

        load_cots_files()

        # Process all the thumbnail images....
        doneLoading = False

        while not doneLoading:
            doneLoading = True
            idx = 0
            while idx < len(g_thumbnail_futures):
                # futil.log(f'Processing thumbnail {idx}...')
                (icon_name, future) = g_thumbnail_futures[idx]
                future : adsk.core.DataObjectFuture = future
                if future.state == adsk.core.FutureStates.ProcessingFutureState:
                    doneLoading = False
                try:
                    if future.dataObject != None:
                        # futil.log(f'Creating thumbnail for {icon_name}')
                        try:
                            os.remove( icon_name )
                        except:
                            pass
                        future.dataObject.saveToFile( icon_name )
                except:
                    futil.handle_error(f'   Error processing thumbnail {icon_name}...')

                idx = idx + 1

        futil.log(f'FolderWalkingThread finished..Loaded {len(g_cots_files)} COTS files.')

        # Fire an event that tells the Palette to refresh.
        app.fireCustomEvent( myCustomEvent, "" ) 
