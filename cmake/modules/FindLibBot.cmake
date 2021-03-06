###############################################################################
# Find package: libbot
#
# This sets the following variables:

# LIBBOT_FOUND
# LIBBOT_INCLUDE_DIRS
# LIBBOT_LIBRARIES

find_package(GTK2 REQUIRED gtk)

find_package(libbot QUIET CONFIG)
set(LIBBOT_FOUND ${libbot_FOUND})
if(libbot_FOUND)
  set(LIBBOT_INCLUDE_DIRS ${GTK2_GLIB_INCLUDE_DIR} ${GTK2_GLIBCONFIG_INCLUDE_DIR})
  set(LIBBOT_LIBRARIES libbot::bot2-core libbot::bot2-frames libbot::bot2-param-client libbot::bot2-lcmgl-client)
  return()
endif()

if(LIBBOT_ROOT)
  set(_include_dir_hint "${LIBBOT_ROOT}/include/bot_core")
  set(_lib_dir_hint "${LIBBOT_ROOT}/lib")
endif()

find_path(LIBBOT_INCLUDE_DIR bot_core/bot_core.h HINTS ${_include_dir_hint} DOC "Path to the include directory containing bot_core")
find_library(LIBBOT_CORE_LIBRARY bot2-core HINTS ${_lib_dir_hint} DOC "The bot-core library")
find_library(LIBBOT_FRAMES_LIBRARY bot2-frames HINTS ${_lib_dir_hint} DOC "The bot-frames library")
find_library(LIBBOT_PARAM_CLIENT_LIBRARY bot2-param-client HINTS ${_lib_dir_hint} DOC "The bot-param-client library")
find_library(LIBBOT_LCMGL_CLIENT_LIBRARY bot2-lcmgl-client HINTS ${_lib_dir_hint} DOC "The bot-lcmgl-client library")
find_library(LIBBOT_LCMGL_RENDERER_LIBRARY bot2-lcmgl-renderer HINTS ${_lib_dir_hint} DOC "The bot-lcmgl-renderer library")

set(LIBBOT_INCLUDE_DIRS ${LIBBOT_INCLUDE_DIR} ${GTK2_GLIB_INCLUDE_DIR} ${GTK2_GLIBCONFIG_INCLUDE_DIR})
set(LIBBOT_LIBRARIES ${LIBBOT_CORE_LIBRARY} ${LIBBOT_FRAMES_LIBRARY} ${LIBBOT_PARAM_CLIENT_LIBRARY} ${LIBBOT_LCMGL_CLIENT_LIBRARY} ${LIBBOT_LCMGL_RENDERER_LIBRARY} ${GTK2_GLIB_LIBRARY})

include(FindPackageHandleStandardArgs)
FIND_PACKAGE_HANDLE_STANDARD_ARGS(LibBot DEFAULT_MSG LIBBOT_INCLUDE_DIR LIBBOT_LIBRARIES)

mark_as_advanced(LIBBOT_INCLUDE_DIR)
mark_as_advanced(LIBBOT_CORE_LIBRARY)
mark_as_advanced(LIBBOT_FRAMES_LIBRARY)
mark_as_advanced(LIBBOT_PARAM_CLIENT_LIBRARY)
mark_as_advanced(LIBBOT_LCMGL_CLIENT_LIBRARY)
mark_as_advanced(LIBBOT_LCMGL_RENDERER_LIBRARY)
