import bmesh, mathutils
from pro import context
from pro import x, y, z
from pro import front, back, left, right, top, bottom, side, all
from pro.op_split import calculateSplit
from .utils import rotation_zNormal_xHorizontal, getEndVertex, verticalNormalThreshold, zAxis

# normal threshold for the Shape3d.comp method to classify if the face is horizontal or vertical
horizontalFaceThreshold = 0.70711 # math.sqrt(0.5)


def getInitialShape(bm):
    """
    Get initial shape out of bmesh
    """
    face = bm.faces[0]
    # check where the face normal is pointing and reverse it, if necessary
    if face.normal[2]<0:
        bmesh.ops.reverse_faces(bm, faces=(face,))
        
    return Shape2d(face.loops[0])


class Shape2d:
    """
    A base class for all 2D shapes
    """
    def __init__(self, firstLoop):
        self.face = firstLoop.face
        self.firstLoop = firstLoop
        # set the origin of the shape coordinate system
        self.origin = firstLoop.vert.co
        # the transformation matrix from the global coordinate system to the shape coordinate system
        self.matrix = None
    
    def extrude(self, depth):
        bm = context.bm
        # store the reference to the original face
        originalFace = self.face
        # execute extrude operator
        geom = bmesh.ops.extrude_face_region(bm, geom=(originalFace,))
        # find extruded face
        for extrudedFace in geom["geom"]:
            if isinstance(extrudedFace, bmesh.types.BMFace):
                break
        # get extruded vertices
        #verts = [v for v in geom["geom"] if isinstance(v, bmesh.types.BMVert)]
        # perform translation along the extrudedFace normal
        bmesh.ops.translate(bm, verts=extrudedFace.verts, vec=depth*extrudedFace.normal)
        
        # find a face connecting originalFace and extrudedFace, that contains the first edge
        # the first edge:
        edge = self.firstLoop.edge
        # loops connected to the first esge
        loops = edge.link_loops
        # find the loop that belong to the face we are looking for (the face connects originalFace and extrudedFace)
        for loop in loops:
            oppositeLoop = loop.link_loop_next.link_loop_next
            oppositeEdgeFaces = oppositeLoop.edge.link_faces
            if oppositeEdgeFaces[0]==extrudedFace or oppositeEdgeFaces[1]==extrudedFace:
                break
        
        # now we have a 3D shape
        # build a list of 2D shapes (faces) that costitute the 3D shape
        shapes = [self, Shape2d(oppositeLoop.link_loops[0])]
        firstLoop = loop
        if extrudedFace.normal[2]>verticalNormalThreshold:
            # first, consider the special case for the horizontal extrudedFace
            while True:
                shapes.append(Rectangle(loop))
                # proceed to the next face connecting originalFace and extrudedFace
                loop = loop.link_loop_next.link_loops[0].link_loop_next
                if loop == firstLoop:
                    break
        else:
            # now, consider the general case
            pass
        
        return Shape3d(shapes, self.firstLoop)

    def getMatrix(self):
        """
        Returns the transformation matrix from the global coordinate system to the shape coordinate system
        """
        if not self.matrix:
            # translationMatrix is inversed translation matrix from the origin of the global coordinate system to the shape origin
            translationMatrix = mathutils.Matrix.Translation(-self.origin)
            # inversed rotation matrix:
            rotationMatrix = rotation_zNormal_xHorizontal(self.firstLoop, self.getNormal())
            # remember inversed(TRS) = inversed(S)*inversed(R)*inversed(T), so in our case:
            self.matrix = rotationMatrix*translationMatrix
        return self.matrix
    
    def getNormal(self):
        """
        Returns the normal to the shape's face.
        A newly created face (instance of BMFace) has a zero normal
        So we have to calculated explicitly
        """
        loop = self.firstLoop
        v1 = getEndVertex(loop).co - loop.vert.co
        loop = loop.link_loop_next
        v2 = getEndVertex(loop).co - loop.vert.co
        normal = v1.cross(v2)
        normal.normalize()
        return normal


class Rectangle(Shape2d):
    
    def __init__(self, firstLoop):
        super().__init__(firstLoop)
    
    def split(self, direction, parts):
        """
        Returns a list of tuples (cutShape, ruleForTheCutShape)
        """
        # we consider that x-axis of the shape coordinate system is oriented along the firstLoop
        
        # referenceLoop is oriented along the positive direction
        referenceLoop = self.firstLoop if direction==x else self.firstLoop.link_loop_next
        # vertices of the referenceLoop
        v = referenceLoop.edge.verts
        cuts = calculateSplit(parts, (v[1].co-v[0].co).length)
        
        bm = context.bm
        
        
        # the loop opposite to referenceLoop
        oppositeLoop = referenceLoop.link_loop_next.link_loop_next
        
        origin1 = referenceLoop.vert
        origin2 = getEndVertex(oppositeLoop)
        
        end1 = getEndVertex(referenceLoop)
        end2 = oppositeLoop.vert
        
        vec1 = end1.co - origin1.co
        vec2 = end2.co - origin2.co
        
        # initial points for a newly cut rectangle 2D-shape
        prevVert1 = origin1
        prevVert2 = origin2
        # the last cut section cuts[-1] is treated separately
        for cutIndex in range(len(cuts)-1):
            cut = cuts[cutIndex]
            cutValue = cut[0]
            v1 = origin1.co + cutValue*vec1
            v2 = origin2.co + cutValue*vec2
            v1 = bm.verts.new(v1)
            v2 = bm.verts.new(v2)
            verts = (prevVert1, v1, v2, prevVert2) if direction==x else (prevVert2, prevVert1, v1, v2)
            # keep the newly cut rectangle 2D-shape in cut[0] 
            cut[0] = self.createSplitShape(bm, verts)
            prevVert1 = v1
            prevVert2 = v2
        # create a face for the last cut section (cutValue=1)
        verts = (prevVert1, end1, end2, prevVert2) if direction==x else (prevVert2, prevVert1, end1, end2)
        cuts[-1][0] = self.createSplitShape(bm, verts)
        
        context.facesForRemoval.append(self.face)
        return cuts
    
    def createSplitShape(self, bm, verts):
        face = bm.faces.new(verts)
        return Rectangle(face.loops[0])
        


class Shape3d:
    """
    Z-axis of the 3D-shape coordinate system is oriented along the z-axis of the global coordinate system.
    X-axis of the 3D-shape coordinate system lies in the first face and parallel to the xy-plane of the global coordinate system.
    If the first face is parallel to the xy-plane of the global coordinate system, then
    x-axis of the 3D-shape coordinate system is oriented along the firstLoop.
    """
    
    def __init__(self, shapes, firstLoop):
        # set 2D shapes (faces) that constitute the 3D-shape
        self.shapes = shapes
        self.firstLoop = firstLoop
        # setting the origin of the shape coordinate system
        self.origin = firstLoop.vert.co
        # rotation matrix is calculated on demand
        self.rotationMatrix = None
    
    def comp(self, parts):
        """
        Returns a dictionary with a comp-selector as the key and a list of 2D-shapes as the related value
        """
        result = {}
        rotationMatrix = self.getRotationMatrix()
        for shape in self.shapes:
            # get normal in the 3D-shape coordinate system
            normal = rotationMatrix * shape.face.normal
            # classify the 2D-shape
            if abs(normal[2]) > horizontalFaceThreshold:
                # the 2D-shape is horizontal
                shapeType = top if normal[2]>0 else bottom
                isVertical = False
            else:
                if abs(normal[0]) > abs(normal[1]):
                    shapeType = front if normal[0]>0 else back
                else:
                    shapeType = right if normal[1]>0 else left
                isVertical = True
            
            if not shapeType in parts:
                if side in parts and isVertical:
                    shapeType = side
                elif all in parts:
                    shapeType = all
                else:
                    shapeType = None
            
            if shapeType:
                if not shapeType in result:
                    result[shapeType] = []
                result[shapeType].append(shape)
        return result
    
    def getRotationMatrix(self):
        if not self.rotationMatrix:
            self.rotationMatrix = rotation_zNormal_xHorizontal(self.firstLoop, zAxis)
        return self.rotationMatrix