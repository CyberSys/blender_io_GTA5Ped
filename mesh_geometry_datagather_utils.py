import bpy
import bmesh
from mathutils import *
from . import mesh_geometry_utils as geomutils


def meshobj_to_geometries(meshObj, parentSkeleton):
    """returns a list of GeometryData objects, one per material, containing the meshObj's relevant data"""
    #make sure there's only one selected object, because we're going to duplicate it
    if len(bpy.context.selected_objects) > 0:
        bpy.ops.object.select_all()
    
    meshObj.select_set(True)
    #also make sure it's the active one
    bpy.context.view_layer.objects.active = meshObj

    #duplicating in these conditions should make the clone be the only one selected and active
    bpy.ops.object.duplicate()

    objCopy = bpy.context.active_object

    #limit weights to 4 per vertex and normalize them
    bpy.ops.object.vertex_group_limit_total(group_select_mode='ALL', limit=4)
    bpy.ops.object.vertex_group_normalize_all(group_select_mode='ALL')

    bpy.ops.object.mode_set( mode = 'EDIT' )

    #split edges by UV islands, because GTA seems to link all islands if their vertices are connected, messing up the map
    bm = bmesh.from_edit_mesh(objCopy.data)
    # old seams
    seams = [e for e in bm.edges if e.seam]
    # unmark
    for e in seams:
        e.seam = False
    # mark seams from uv islands
    bpy.ops.uv.seams_from_islands()
    seams = [e for e in bm.edges if e.seam]
    # split on seams
    bmesh.ops.split_edges(bm, edges=seams)

    bmesh.update_edit_mesh(objCopy.data)

    bm.free()

    #separate and mark each object with its "shaderIndex"
    #but keep the last piece in this object, so that we don't end up with an extra empty geometry
    for i in range(len(objCopy.material_slots) - 1, 0, -1):
        objCopy.active_material_index = i
        objCopy["Gta5MatIndex"] = i
        bpy.ops.object.material_slot_select()
        bpy.ops.mesh.separate()

    objCopy["Gta5MatIndex"] = 0

    #edit mode changes don't seem to take place until we change mode to object
    bpy.ops.object.mode_set( mode = 'OBJECT' )

    resultingGeometries = []

    #after separating, all pieces are selected
    for obj in bpy.context.selected_objects:
        resultingGeometries.append(parse_obj_to_geometrydata(obj, parentSkeleton, obj["Gta5MatIndex"]))

    #delete duplicates now
    for obj in bpy.context.selected_objects:
        geomutils.delete_mesh(obj.data)

    return resultingGeometries


def parse_obj_to_geometrydata(meshObj, parentSkeleton, shaderIndex):
    """parses a single-material mesh into a GeometryData object"""

    theMesh = meshObj.data

    theMesh.calc_tangents()

    geom = geomutils.GeometryData()

    bm = bmesh.new()
    bm.from_mesh(theMesh)

    geom.shaderIndex = shaderIndex

    deformlayer = bm.verts.layers.deform.verify()

    skelData = None

    if parentSkeleton is not None:
        skelData = parentSkeleton.data
    
    boneIndexTranslation = {} #vertex group index to skeleton bone index


    #vert positions and normals...
    for vert in bm.verts:
        geom.vertPositions.append(vert.co.copy())
        geom.vertNormals.append(vert.normal.copy())

        #weights...
        if skelData is not None:
            vertBoneIndexes = []
            vertBoneWeights = []
            for groupIndex, weight in vert[deformlayer].items():
                if boneIndexTranslation.get(groupIndex, -1) == -1:
                    boneIndexTranslation[groupIndex] = skelData.bones.find(meshObj.vertex_groups[groupIndex].name)
                
                vertBoneIndexes.append(boneIndexTranslation[groupIndex])
                vertBoneWeights.append(weight)

            #if this vert has less than 4 bones with weights, pad the indexes and weights lists with empty entries
            vertBoneIndexes += [0] * (4 - len(vertBoneIndexes))
            vertBoneWeights += [0.0] * (4 - len(vertBoneWeights))

            geom.boneIndexes.append(vertBoneIndexes)
            geom.boneWeights.append(vertBoneWeights)

        else:
            geom.boneIndexes.append([0] * 4)
            geom.boneWeights.append([0.0] * 4)
        
            
    uvlayer = bm.loops.layers.uv.verify()
    #fill uvCoords and qtangents with blank entries so that we can fill them in any order
    geom.uvCoords = [(0.0, 0.0)] * len(geom.vertPositions)
    geom.qtangents = [(0.0, 0.0, 0.0, 0.0)] * len(geom.vertPositions)

    #indices and uv
    for face in bm.faces:
        for vert in face.verts:
            geom.indices.append(vert.index)
        for loop in face.loops:
            if geom.uvCoords[loop.vert.index] == (0.0, 0.0): #only parse this vert if we still don't have this data about it
                geom.uvCoords[loop.vert.index] = loop[uvlayer].uv.copy()
                geom.uvCoords[loop.vert.index].y *= -1
                geom.qtangents[loop.vert.index] = get_loop_qtangent(theMesh.loops[loop.index])

                

    #finally, calculate and store bounds
    geom.calculate_geometry_bounds()

    bm.free()

    return geom


def get_loop_qtangent(loop):
    """returns a quaternion containing combined tangent data from the target loop 
    (mesh.calc_tangents should already have been called!)"""

    loopMatrix = Matrix([
        loop.bitangent,
        loop.tangent,
        loop.normal
        ])
    
    if loopMatrix.determinant() < 0:
        loopMatrix[2] = loopMatrix[2] * -1
        
    loopQuat = loopMatrix.to_quaternion()

    return list(reversed(loopQuat)) #apparently, the quaternion is stored as ZYXW instead of WXYZ in the .mesh file