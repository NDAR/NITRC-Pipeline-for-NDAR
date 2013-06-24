import sys
import os
import tempfile
import csv
import shutil
import zipfile
try:
    from boto.s3.connection import OrdinaryCallingFormat, S3Connection
    import boto.s3.key
except ImportError:
    pass
try:
    import MySQLdb
except ImportError:
    pass

def NDARError(Exception):
    """base class for exceptions"""

def ObjectNotFoundError(NDARError):
    """S3 object not found"""

def _get_file_type(fname):
    """Return the type of a file."""
    if fname.endswith('.nii.gz'):
        return 'NIfTI-1'
    if fname.endswith('.png'):
        return 'PNG'
    if fname.endswith('.jpg'):
        return 'JPEG'
    if fname.endswith('.mnc'):
        return 'MINC'
    if fname.endswith('.nrrd'):
        return 'NRRD'
    if fname.endswith('.HEAD') or fname.endswith('.BRIK'):
        return 'BRIK'
    with open(fname) as fo:
        fo.seek(128)
        if fo.read(4) == 'DICM':
            return 'DICOM'
    return 'other'

class _BaseImage(object):

    """base class for images"""

    def __init__(self):
        # this is set to true once the temporary directory has been 
        # created -- we just need it set now in case we fail before then 
        # and __del__() is called
        self._clean = False
        self._tempdir = tempfile.mkdtemp()
        self._clean = True
        return

    def _unpack(self):

        os.mkdir('%s/unpacked' % self._tempdir)
        self.files = {'DICOM': [], 
                      'NIfTI-1': [], 
                      'MINC': [], 
                      'BRIK': [], 
                      'NRRD': [], 
                      'PNG': [], 
                      'JPEG': [], 
                      'other': []}
        if self.source.endswith('.zip'):
            zf = zipfile.ZipFile(self._temp_source)
            zf.extractall('%s/unpacked' % self._tempdir)
            for fname in os.listdir('%s/unpacked' % self._tempdir):
                full_path = '%s/unpacked/%s' % (self._tempdir, fname)
                self.files[_get_file_type(full_path)].append(fname)
        else:
            file_type = _get_file_type(self.source)
            self.files[file_type].append(self._source_base)
            os.symlink(self._temp_source, self.path(self._source_base))

        # now match up .HEADs and .BRIKs
        heads = set([ name[:-5] for name in self.files['BRIK'] \
                                if name.endswith('.HEAD') ])
        briks = set([ name[:-5] for name in self.files['BRIK'] \
                                if name.endswith('.BRIK') ])
        pairs = heads.intersection(briks)
        self.files['BRIK'] = list(pairs)
        lone_heads = [ base+'.HEAD' for base in heads-pairs ]
        lone_briks = [ base+'.BRIK' for base in briks-pairs ]
        self.files['other'].extend(lone_heads)
        self.files['other'].extend(lone_briks)

        # sort the file names
        for l in self.files.itervalues():
            l.sort()

        return

    def __del__(self):
        self.close()
        return

    def close(self):
        """Clean up temporary files."""
        if self._clean:
            shutil.rmtree(self._tempdir)
        return

    def path(self, fname):
        """Return the full path to a single file."""
        return '%s/unpacked/%s' % (self._tempdir, fname)

class Image(_BaseImage):

    """image-from-file class"""

    def __init__(self, source):
        _BaseImage.__init__(self)
        self.source = os.path.abspath(source)
        if not os.path.exists(self.source):
            raise IOError(errno.ENOENT, 
                          "No such file or directory: '%s'" % self.source)
        self.files = None
        return

    def __getattribute__(self, name):
        value = _BaseImage.__getattribute__(self, name)
        if name == 'files' and value is None:
            self._source_base = os.path.basename(self.source)
            self._temp_source = '%s/%s' % (self._tempdir, self._source_base)
            os.symlink(self.source, self._temp_source)
            self._unpack()
            return self.files
        return value

class S3Image(_BaseImage):

    """image-from-S3 class"""

    def __init__(self, source, s3_access_key, s3_secret_key):
        _BaseImage.__init__(self)
        if 'boto' not in sys.modules:
            raise ImportError('boto S3 connection module not found')
        self.source = source
        self._s3_access_key = s3_access_key
        self._s3_secret_key = s3_secret_key
        # even though we don't actually download the object here, we check 
        # it so exceptions are raised at instantiation
        # source is 's3://bucket/path/to/object'
        (bucket_name, object_name) = self.source[5:].split('/', 1)
        s3 = S3Connection(self._s3_access_key, 
                          self._s3_secret_key, 
                          calling_format=OrdinaryCallingFormat())
        bucket = s3.get_bucket(bucket_name)
        key = boto.s3.key.Key(bucket)
        key.key = object_name
        if not key.exists():
            raise ObjectNotFoundError()
        s3.close()
        self.files = None
        return

    def __getattribute__(self, name):
        value = _BaseImage.__getattribute__(self, name)
        if name == 'files' and value is None:
            # source is 's3://bucket/path/to/object'
            (bucket_name, object_name) = self.source[5:].split('/', 1)
            s3 = S3Connection(self._s3_access_key, 
                              self._s3_secret_key, 
                              calling_format=OrdinaryCallingFormat())
            bucket = s3.get_bucket(bucket_name)
            key = boto.s3.key.Key(bucket)
            key.key = object_name
            self._source_base = os.path.basename(self.source)
            self._temp_source = '%s/%s' % (self._tempdir, self._source_base)
            key.get_contents_to_filename(self._temp_source)
            s3.close()
            self._unpack()
            return self.files
        return value

class _BasePackage:

    """base class for packages"""

    def __init__(self):
        return

class Package(_BasePackage):

    """package on disk"""

    def __init__(self, path):
        _BasePackage.__init__(self)
        self.path = path
        self.images = []
        fo = open('%s/image03.txt' % self.path)
        r = csv.reader(fo, delimiter='\t')
        headers = r.next()
        # unused
        description = r.next()
        self.images = [ dict(zip(headers, row)) for row in r ]
        fo.close()
        return

    def image(self, image_file):
        return Image('%s/image03/%s' % (self.path, image_file))

class MySQLPackage(_BasePackage):

    """package from a mysql database"""

    def __init__(self, db_host, db_user, db_password, database):
        if 'MySQLdb' not in sys.modules:
            raise ImportError('MySQLdb module not found')
        _BasePackage.__init__(self)
        db = MySQLdb.connect(db_host, db_user, db_password, database)
        c = db.cursor()
        c.execute('SELECT * FROM image03')
        cols = [ el[0] for el in c.description ]
        self.images = [ dict(zip(cols, row)) for row in c ]
        db.close()
        return

    def image(self, image_url, s3_access_key, s3_secret_key):
        return S3Image(image_url, s3_access_key, s3_secret_key)

# eof
