import adsk.core
import os
import threading
import time
import heapq
import json
import re

from .lib import fusionAddInUtils as futil
from . import config

app = adsk.core.Application.get()
ui = app.userInterface

myCustomEvent = 'FRC_COTS_DatabaseThreadEvent'

# Global state
g_parts_db = None        # PartsDatabase object
g_parts_db_io = None     # PartsDatabaseFileIO object

g_palette_ready = False     # HTML palette is fully loaded

def sanitize_part_name( part_name:str ):
    safeName = re.sub(r'\W+','', part_name)
    return safeName

def flatten_path( path:str ):
    flat_path = path.replace( '\\', '_' )
    flat_path = flat_path.replace( '/', '_' )
    return flat_path

def get_icon_filename( path:str, basename:str ):
    """Path to the favorites JSON file next to this add-in."""
    safeName = sanitize_part_name( basename )
    flat_path = flatten_path(path)
    folder = config.PARTS_DB_FOLDER
    return os.path.join(folder, 'icons', f'{flat_path}{safeName}.png')

class FolderRecord:
    def __init__(self, path, parent):
        self.childFolders = []
        self.parentFolder = parent
        self.files = []
        self.path = path
        self.dataFolder = None

    def add_child(self, child):
        

class FileRecord:
    def __init__(self, parent):
        self.parentFolder = parent
        self.dataFile = None

class PartsDatabaseFileIO:
    def __init__(self, project: adsk.core.DataProject):
        # self.data_files = {}
        self.project = project
        self.rootRec = FolderRecord( '/', None )
        self.rootRec.dataFolder = self.project.rootFolder
        self.thumbnails = {}

    def get_data_file(self, path, id):
        futil.log( f'get_data_file() -- Getting data file at {path} with id={id}...')
        if id in self.data_files:
            futil.log( f'    id is already loaded.')
            return self.data_files[id][0]
        
        folder = self.get_data_folder(path)
        if not folder:
            futil.log( f'    Cannot find dataFolder {path}.')
            return None
        
        self.load_folder_files( path, folder )
        return self.data_files[id][0]

    def get_all_data_files(self, path):
        pass

    def get_data_folder(self, path: str):
        # Check if we already have this folder stored
        if path in self.data_folders:
            return self.data_folders[path]
        
        # Find the closest sub-path that we have stored.
        # We have the root folder stored so that would be
        # the stopping point if nothing is loaded yet.
        sub_path = path
        while len(sub_path) > 0:
            sub_path = sub_path[0:sub_path.rfind('/')]
            futil.log(f"get_data_folder() -- Looking at subfolder '{sub_path}'.")
            if sub_path in self.data_folders:
                break
        
        # We didn't find a sub folder.  Return the root folder.
        if len(sub_path) == 0:
            return self.data_folders['/']
        
        # Start looking for the unloaded part of the folder path
        sub_folder: adsk.core.DataFolder = self.data_folders[sub_path]
        path_parts = path.split('/')[len(sub_path.split('/')):]
        dfolder = self.find_folder_with_path( sub_path, sub_folder, path_parts )

        # We didn't find the requested folder
        if not dfolder:
            futil.log(f"   Could not find data folder '{path}'.")
            return None

        return dfolder

    def find_folder_with_path( self, start_path: str, startFolder: adsk.core.DataFolder, path_parts: str ):
        # Look for a folder that is a sub-folder of the startFolder
        # path_parts is a List of folders that we are looking for
        # when that list has a length of 1 we have found the folder we
        # are looking for.
        dfolder = startFolder.dataFolders.itemByName( path_parts[0] )
        if not dfolder:
            futil.log( f'find_folder_with_path() Error finding folder {start_path}{path_parts[0]}...')
            return None

        dfolder_path = start_path + dfolder.name + '/'
        self.data_folders[dfolder_path] = dfolder

        # We are done.  Return the folder
        if len(path_parts) == 1:
            return dfolder
        
        # Look inside the new folder since length of path_parts is greater than 1
        return self.find_folder_with_path( dfolder_path, dfolder, path_parts[1:] )

    def search_folder( self, path, folder: adsk.core.DataFolder, data_file_id ):
        idx = 0
        for df in folder.dataFiles:
            self.data_files[df.id] = (df, path)
            icon_name = get_icon_filename( path, df.name )
            self.thumbnails[df.id] = (icon_name, df.thumbnail)
            idx = idx + 1
            if df.id == data_file_id:
                return df

        futil.log( f'Folder {path}, loaded {idx} files...')

        for fd in folder.dataFolders :
            new_path = path + fd.name + '/'
            df = self.search_folder( new_path, fd, data_file_id )
            if df:
                return df


    def load_folder_files(self, path, dfolder: adsk.core.DataFolder) -> dict[str, adsk.core.DataFile]:
        dataf = {}
        for df in dfolder.dataFiles:
            if df.fileExtension and df.fileExtension.lower() == 'f3d':
                if df.id not in self.data_files:
                    icon_name = get_icon_filename( path, df.name )
                    self.thumbnails[df.id] = (icon_name, df.thumbnail)
                    self.data_files[df.id] = (df, path)
                    dataf[df.id] = df

        return dataf

    def load_all_files(self):
        self.search_folder( '', self.data_folders['/'], None )

    def process_thumbnails(self):
        doneLoading = False
        while not doneLoading:
            doneLoading = True
            idx = 0
            for thumb_id in self.thumbnails:
                # futil.log(f'Processing thumbnail {idx}...')
                thumb = self.thumbnails[thumb_id]
                icon_name = thumb[0]
                future : adsk.core.DataObjectFuture = thumb[1]
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

class PartsDatabase:
    JSON_FILE = 'parts_db.json'

    def __init__(self, io: PartsDatabaseFileIO):
        self.io = io
        self.mutex = threading.Lock()
        self.part_list = []
        self.database = {}
        self.database['project'] = {}
        self.database['parts'] = {}

        if not self.load_json_file():
            self.blank_database()
        
        if self.database['project']['name'] != self.io.project.name:
            # The parts db is for a different project!
            futil.log(f'JSON project and the settings project do not match!')
            futil.log(f'   Regenerating the JSON database...')
            self.blank_database()

        if len(self.database['parts']) == 0:
            self.update_folder('/')

        self.build_part_list()

    def blank_database(self):
        self.database = {}
        self.database['project'] = {'name': self.io.project.name, 'id': self.io.project.id }
        self.database['parts'] = {}
        self.mutex.acquire()
        self.part_list = []
        self.mutex.release()

    def add_part(self, id, path, name, version, icon_name):
        self.mutex.acquire()
        # heapq.heappush( self.sorted_list, (path, name, id, icon_name ))
        self.part_list.append((path, name, id, icon_name ))
        self.mutex.release()
        self.database['parts'][id] = { "path": path,
                                       "name": name,
                                       "version": version, 
                                       "icon": icon_name }

    def remove_part(self, id, path, name):
        try:
            self.mutex.acquire()
            for part in self.part_list:
                if part[0] == path and part[1] == name:
                    self.part_list.remove(part)
                    self.database['parts'].pop(id)
                    break
        finally:
            self.mutex.release()


    def add_folder_placeholder(self, path):
        id = path + '_placeholder_'
        self.add_part(id, path, '', '', '')

    def remove_folder_placeholder(self, path):
        id = path + '_placeholder_'
        self.remove_part(id, path, '')

    def get_part(self, id):
        try:
            part = self.database['parts'][id]
            return part
        except:
            return None
        
    def get_sorted_list(self):
        self.mutex.acquire()
        sorted_list = sorted( self.part_list, key=lambda part: part[0]+part[1] )
        self.mutex.release()
        return sorted_list

    def build_part_list(self):
        self.mutex.acquire()
        self.part_list = []
        self.mutex.release()
        for file_id in self.database['parts']:
            p = self.database['parts'][file_id]
            self.mutex.acquire()
            # heapq.heappush( self.sorted_list, (p['path'], p['name'], file_id, p['icon'] ))
            self.part_list.append((p['path'], p['name'], file_id, p['icon']))
            self.mutex.release()

    def database_loaded(self):
        return len(self.part_list) > 0

    def update_folder(self, path):
        dfolder = self.io.get_data_folder( path )
        if not dfolder:
            futil.log( f'update_folder() -- Error loading "{path}".')
            return False
        
        for folder in dfolder.dataFolders:
            self.add_folder_placeholder(path + folder.name + '/')

        files = self.io.load_folder_files( path, dfolder )
        for fid in files:
            df = files[fid]
            icon = get_icon_filename( path, df.name )
            self.add_part(fid, path, df.name, df.versionNumber, icon)


    def update_parts_database(self):
        if not self.database_loaded():
            self.create_parts_database()
            return

        # For the FileIO to load all the files so we can check for
        # any added files that are not in the JSON database
        self.io.load_all_files()

        
    def create_parts_database(self):
        self.io.load_all_files()
        for part_id in self.io.data_files:
            (df, path) = self.io.data_files[part_id]
            icon_name = get_icon_filename( path, df.name )
            self.add_part( part_id, path, df.name, df.versionNumber, icon_name )


    def load_json_file(self):
        db_filename = os.path.join(config.PARTS_DB_PATH, PartsDatabase.JSON_FILE)
        if os.path.exists(db_filename):
            try:
                with open(db_filename, 'r') as f:
                    self.database = json.load(f)
                return True
            except:
                futil.log( f'Could not open parts db JSON file {db_filename} for reading...')
        else:
            futil.log( f'Parts db JSON file {db_filename} does not exist...')
        return False

    def save_json_file(self):
        db_filename = os.path.join( config.PARTS_DB_PATH, PartsDatabase.JSON_FILE )
        try:
            with open(db_filename, 'w') as f:
                json.dump(self.database, f, indent=2)
        except Exception:
            futil.handle_error(f"Could not write parts database file '{db_filename}'.")

class DBWorkItemType:
    FOLDER = 0
    THUMBNAIL = 1

class PartsDatabaseWorkItem:
    type: DBWorkItemType = DBWorkItemType.FOLDER


class PartsDatabaseWorkQueue:
    def __init__(self, db: PartsDatabase):
        self.db = db
        self.tasks = []

    def add_folder_task(self, folder: adsk.core.DataFolder):
        pass


def get_data_file( path, data_file_id ):
    global g_parts_db_io

    df_entry = g_parts_db_io.get_data_file( path, data_file_id )
    if not df_entry:
        return None
    
    return df_entry[0]

def load_folder( path ):
    global g_parts_db

    g_parts_db.update_folder( path )

def get_sorted_database_list():
    global g_parts_db

    if not g_parts_db:
        return {}
    
    return g_parts_db.get_sorted_list()







# def get_project():
#     global g_frc_cots_proj
#     return g_frc_cots_proj

# def get_parts_db_json():
#     global g_parts_db

#     g_parts_db = {}
#     folder = os.path.dirname(__file__)
#     db_path = os.path.join( folder, 'parts_db' )
#     try:
#         os.makedirs( db_path, 511, True )
#     except:
#         futil.popup_error(f"Could not create parts database directory '{db_path}'.")
#         return
    
#     db_filename = os.path.join(db_path, 'parts_db.json')
#     if os.path.exists(db_filename):
#         with open(db_filename, 'r') as f:
#             g_parts_db = json.load(f)
    
# def write_parts_db_json():
#     global g_parts_db

#     folder = os.path.dirname(__file__)
#     db_filename = os.path.join( folder, 'parts_db', 'parts_db.json' )
#     try:
#         with open(db_filename, 'w') as f:
#             json.dump(g_parts_db, f, indent=2)
#     except Exception:
#         futil.popup_error(f"Could not write parts database file '{db_filename}'.")


def search_folder( folder: adsk.core.DataFolder, data_file_id ):
    for f in folder.dataFiles :
        if f.id == data_file_id:
           return f

    for fd in folder.dataFolders :
        df = search_folder( fd, data_file_id )
        if df:
            return df
        
    return None

# def open_data_file( data_file_id ):
#     root = get_project().rootFolder

#     data_file = search_folder( root, data_file_id )
#     if not data_file:
#         futil.log( f'Cannot file datafile with id {data_file_id}.')
#         return None
    
#     return data_file


def _enumerate_folders( folder: adsk.core.DataFolder, path, out_list):
    out_list.append( (folder, path) )

    for sub in folder.dataFolders:
        sub_path = path + sub.name + '/'
        _enumerate_folders( sub, sub_path, out_list)

def _walk_folder(folder: adsk.core.DataFolder, prefix, out_list):
    """Recursively walk a DataFolder and append (prefix, label, DataFileId, icon_name) for .f3d files."""
    global g_parts_db
    global g_cots_lock
    global g_cots_file_io

    app.fireCustomEvent( myCustomEvent, "update" ) 
    idx = 0
    for df in folder.dataFiles:        
        try:
            if df.fileExtension and df.fileExtension.lower() == 'f3d':
                part_name = df.name
                icon_name = get_icon_filename( prefix, part_name )
                if g_cots_lock.acquire( True, 0.5):
                    heapq.heappush( out_list, (prefix, part_name, df.id, icon_name))
                    g_cots_lock.release()
                else:
                    futil.log( 'Cannot lock the g_cots_files list...')
                g_cots_file_io[df.id] = [df, icon_name, df.thumbnail]
                g_parts_db['parts'][df.id] = {"prefix": prefix,
                                              "name": part_name,
                                              "version": df.versionNumber, 
                                              "icon": icon_name }
            
                idx = idx + 1
        except:
            futil.log(f'Failed to load file {df.name}.')
            continue
    
    futil.log( f'Folder {prefix}{folder.name}, loaded {idx} files...')

    # Subfolders
    for sub in folder.dataFolders:
        new_prefix = prefix + sub.name + '/'
        _walk_folder(sub, new_prefix, out_list)

def find_project():
    try:
        data = app.data
        projects = data.dataProjects

        frc_project = None
        for proj in projects:
            if proj.name == config.PARTS_DB_PROJECT:
                frc_project = proj
                break

        if not frc_project:
            futil.log(f"Could not find project '{config.PARTS_DB_PROJECT}'.")
            return frc_project
    except:
        futil.handle_error(f"Error loading project '{config.PARTS_DB_PROJECT}'.")

    return frc_project

def load_cots_files():
    """Populate g_cots_sorted with all .f3d files under the FRC_COTS project."""
    global g_cots_sorted
    global g_frc_cots_proj

    g_cots_sorted = []

    futil.log(f'Loading COTS files....')

    find_project()
    try:
        root = g_frc_cots_proj.rootFolder

        _walk_folder(root, '', g_cots_sorted)
        futil.log( f'Loaded {len(g_cots_sorted)} COTS files...')

    except:
        futil.handle_error( "Load COTS files" )
        g_cots_sorted = []

def add_db_entry( path: str, df: adsk.core.DataFile ):
    global g_cots_file_io
    global g_parts_db

    part_id = df.id
    icon_name = get_icon_filename( path, df.name )
    if g_cots_lock.acquire( True, 0.5):
        heapq.heappush( g_cots_sorted, (path, df.name, df.id, icon_name))
        g_cots_lock.release()
    else:
        futil.log( 'Cannot lock the g_cots_files list...')
    g_parts_db['parts'][df.id] = {"prefix": path,
                                "name": df.name,
                                "version": df.versionNumber, 
                                "icon": icon_name }
    g_cots_file_io[part_id] = [df, icon_name, df.thumbnail]

def check_db_entry( df: adsk.core.DataFile ):
    global g_cots_file_io
    global g_parts_db

    part_id = df.id
    db_record = g_parts_db['parts'][part_id]
    icon_name = db_record['icon']
    if df.versionNumber != db_record['version']:
        # The version in the json database does not match the file version
        # Start loading a new thumbnail image
        g_cots_file_io[part_id] = [df, icon_name, df.thumbnail]
    else:
        # The check if the icon file exists
        if not os.path.isfile( icon_name ):
            g_cots_file_io[part_id] = [df, icon_name, df.thumbnail]


def update_cots_files():
    global g_cots_sorted
    global g_frc_cots_proj
    global g_cots_file_io
    global g_parts_db

    find_project()

    folders = []
    _enumerate_folders( g_frc_cots_proj.rootFolder, "/", folders )

    for (folder, path) in folders:
        futil.log(f'update_cots_files() -- Processing folder {path}...')
        for df in folder.dataFiles:
            df: adsk.core.DataFile = df
            if df.fileExtension and df.fileExtension.lower() == 'f3d':
                # We have a part file....
                part_id = df.id
                if part_id in g_parts_db['parts']:
                    # This part file is stored in the json database
                    check_db_entry( df )
                else:
                    # This part is not in the json database
                    # Need to add it to the json database and the sorted files list
                    add_db_entry( path, df )


class DatabaseThread(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
        self.stopped = threading.Event()

    def isRunning(self):
        return not self.stopped.is_set()

    def stop(self):
        self.stopped.set()

    def run(self):
        # global g_cots_sorted
        # global g_cots_file_io
        # global g_parts_db
        global g_parts_db
        global g_parts_db_io
        global g_palette_ready

        try:
            futil.log(f'DatabaseThread::run()...')

            # Create the parts file IO database
            g_parts_db_io = PartsDatabaseFileIO( find_project() )

            # Load the parts database
            g_parts_db = PartsDatabase(g_parts_db_io)


            if g_parts_db.database_loaded():
                idx = 1
                while not g_palette_ready:
                    futil.log(f'DatabaseThread::run() -- Waiting to activate palette {idx}...')
                    time.sleep( 0.1 )
                    idx = idx + 1
                    if idx > 20:
                        break

                app.fireCustomEvent( myCustomEvent, "activate" )

            if self.stopped.is_set():
                return

            # g_parts_db.update_parts_database( g_parts_db_io )

            g_parts_db_io.process_thumbnails()
            g_parts_db.save_json_file()

            # if len(g_parts_db) == 0:
            #     # No parts db was found
            #     g_parts_db['project'] = config.PARTS_DB
            #     g_parts_db['parts'] = {}
            #     load_cots_files()
            # else:
            #     # A parts db file was found
            #     if g_parts_db['project'] != config.PARTS_DB:
            #         # The parts db is for a different project!
            #         g_parts_db = {}
            #         g_parts_db['project'] = config.PARTS_DB
            #         g_parts_db['parts'] = {}
            #         load_cots_files()
            #     else:
            #         # We have a good parts db for the correct project
            #         for file_id in g_parts_db['parts']:
            #             p = g_parts_db['parts'][file_id]
            #             heapq.heappush( g_cots_sorted, (p['prefix'], p['name'], file_id, p['icon'] ))

            #         # We have an existing database so enable the UI
            #         app.fireCustomEvent( myCustomEvent, "done" ) 
            #         update_cots_files()


            # Process all the thumbnail images....
            # doneLoading = False

            # while not doneLoading:
            #     doneLoading = True
            #     idx = 0
            #     for part_id in g_cots_file_io:
            #         # futil.log(f'Processing thumbnail {idx}...')
            #         file_io = g_cots_file_io[part_id]
            #         icon_name = file_io[1]
            #         future : adsk.core.DataObjectFuture = file_io[2]
            #         if future.state == adsk.core.FutureStates.ProcessingFutureState:
            #             doneLoading = False
            #         try:
            #             if future.dataObject != None:
            #                 # futil.log(f'Creating thumbnail for {icon_name}')
            #                 try:
            #                     os.remove( icon_name )
            #                 except:
            #                     pass
            #                 future.dataObject.saveToFile( icon_name )
            #         except:
            #             futil.handle_error(f'   Error processing thumbnail {icon_name}...')

            #         idx = idx + 1

            # futil.log(f'FolderWalkingThread finished..Loaded {len(g_cots_sorted)} COTS files.')

            # write_parts_db_json()

            # Fire an event that tells the Palette to refresh.
            app.fireCustomEvent( myCustomEvent, "update" )

            while self.isRunning():
                time.sleep( 0.25 )

            futil.log(f'DatabaseThread() -- Finishing normally...')

        except:
            futil.handle_error( "DatabaseThread" )