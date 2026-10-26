"""Hong Kong Map Services QGIS plugin."""


def classFactory(iface):
    """Return the plugin instance QGIS calls on load.

    The plugin class is imported here so unit tests can import the catalog
    without starting QGIS.
    """
    from .hkmapservices import HkMapServices

    return HkMapServices(iface)
