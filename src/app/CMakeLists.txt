find_package(PythonQt REQUIRED)
find_package(CTKPythonConsole REQUIRED)
find_package(OpenGL REQUIRED)
find_package(QtPropertyBrowser REQUIRED)
if(DD_QT_VERSION EQUAL 4)
  include(${QT_USE_FILE})
else()
  include_directories(${Qt5Core_INCLUDE_DIRS})
endif()

find_package(VTK REQUIRED COMPONENTS
  vtkWrappingPythonCore
  vtkGUISupportQt
  vtkInteractionWidgets
  vtkRenderingAnnotation
  vtkRenderingFreeType
  vtkInteractionStyle
  vtkIOXML
  vtkIOImage
  vtkIOGeometry
  )
include(${VTK_USE_FILE})
check_vtk_qt_version()

include_directories(${CMAKE_CURRENT_SOURCE_DIR})
include_directories(${CMAKE_CURRENT_BINARY_DIR})
include_directories(${CMAKE_SOURCE_DIR}/src/common)
include_directories(${QTPROPERTYBROWSER_INCLUDE_DIRS})
include_directories(${CTK_PYTHONCONSOLE_INCLUDE_DIRS})
include_directories(${PYTHONQT_INCLUDE_DIRS})
include_directories(${PYTHON_INCLUDE_DIRS})

set(decorator_file ${CMAKE_CURRENT_BINARY_DIR}/ddPythonQtDecorators.h)

# Configure whether to use the QVTKWidget or the QVTKOpenGLWidget
set(USE_QVTKWIDGET TRUE)
if((${Qt5Core_VERSION} VERSION_GREATER "5.4.0") AND
   (NOT ${VTK_VERSION} VERSION_LESS "8.0.0") AND
   (${VTK_RENDERING_BACKEND} STREQUAL "OpenGL2"))
  set(USE_QVTKWIDGET FALSE)
endif()
configure_file(${CMAKE_CURRENT_SOURCE_DIR}/ddQVTKOpenGLWidgetConfigure.h.in
               ${CMAKE_CURRENT_BINARY_DIR}/ddQVTKOpenGLWidgetConfigure.h)

if(USE_QVTKWIDGET)
  file(WRITE ${CMAKE_CURRENT_BINARY_DIR}/wrapped_methods_qvtk.txt
    "QVTKWidget* ddQVTKWidgetView::vtkWidget() const;"
    )
else()
  file(WRITE ${CMAKE_CURRENT_BINARY_DIR}/wrapped_methods_qvtk.txt
    "QVTKOpenGLWidget* ddQVTKWidgetView::vtkWidget() const;"
    )
endif()

set(wrap_files
  wrapped_methods.txt
  ${CMAKE_CURRENT_BINARY_DIR}/wrapped_methods_qvtk.txt
  )

qt_wrap_cpp(moc_srcs
  ${decorator_file}
  ddLumberSelection.h
  ddGLWidgetView.h
  ddGroupBoxHider.h
  ddMacrosManager.h
  ddMainWindow.h
  ddObjectTree.h
  ddPropertiesPanel.h
  ddPythonEventFilter.h
  ddPythonManager.h
  ddPythonUndoCommand.h
  ddQVTKWidgetView.h
  ddSignalMap.h
  ddSpreadsheetView.h
  ddTaskSelection.h
  ddViewBase.h
  ddViewManager.h
  ddViewMenu.h
  ddVTKObjectMap.h
  )

qt_wrap_ui(ui_srcs
  ddMainWindow.ui
  ddLumberSelection.ui
  ddTaskSelection.ui
  )

qt_add_resources(resource_srcs
  uiresources.qrc
  images/resources.qrc
  )

set(srcs
  ${moc_srcs}
  ${ui_srcs}
  ${resource_srcs}

  QVTKOpenGLInit.cpp
  ddGLWidgetView.cpp
  ddLumberSelection.cpp
  ddMacrosManager.cpp
  ddMainWindow.cpp
  ddObjectTree.cpp
  ddPropertiesPanel.cpp
  ddPythonApp.cpp
  ddPythonManager.cpp
  ddPythonQtWrapperFactory.cpp
  ddPythonUndoCommand.cpp
  ddQVTKWidgetView.cpp
  ddSignalMap.cpp
  ddSpreadsheetView.cpp
  ddTaskSelection.cpp
  ddViewBase.cpp
  ddViewManager.cpp
  ddViewMenu.cpp
  ddVTKObjectMap.cpp

  vtkSimpleActorInteractor.cxx
  vtkTDxInteractorStyleCallback.cxx
  )

set(deps
  ${QT_LIBRARIES}
  ${CTK_PYTHONCONSOLE_LIBRARIES}
  ${PYTHONQT_LIBRARIES}
  ${QTPROPERTYBROWSER_LIBRARY}
  ${OPENGL_LIBRARIES}
  ${VTK_LIBRARIES}
  )

set(pkg_deps)




#####

if (USE_DRAKE)

  find_package(Drake REQUIRED MODULE)
  find_package(Eigen REQUIRED)

  include_directories(
    ${EIGEN_INCLUDE_DIRS}
    ${DRAKE_INCLUDE_DIRS}
  )

  set(moc_srcs)
  qt_wrap_cpp(moc_srcs
    ddDrakeModel.h
    ddDrakeWrapper.h
  )

  list(APPEND srcs
    ${moc_srcs}
    ddDrakeWrapper.cpp
  )

  # openhumanoids uses an old drake fork which has a divergent api so we
  # use a fork of ddDrakeModel.cpp to encapsulate the api incompatibilities
  if (USE_DRC)
    set(DRAKE_OH_FORK TRUE)
    list(APPEND srcs ddDrakeModelOH.cpp)
  else()
    set(DRAKE_OH_FORK FALSE)
    list(APPEND srcs ddDrakeModel.cpp)
  endif()

  configure_file(ddDrakeVersion.h.in ddDrakeVersion.h)

  list(APPEND wrap_files
    wrapped_methods_drake.txt
  )

  list(APPEND deps
    ${DRAKE_LIBRARIES}
  )

endif()

if (USE_LCM)

  find_package(LCM REQUIRED)
  include_directories(${LCM_INCLUDE_DIRS})

  set(moc_srcs)
  qt_wrap_cpp(moc_srcs
    ddLCMSubscriber.h
    ddLCMThread.h
  )

  list(APPEND srcs
    ${moc_srcs}
    ddLCMThread.cpp
  )

  if(NOT BUILD_SHARED_LIBS AND LCM_STATIC_LIBRARIES)
    set(LCM_LIBRARIES ${LCM_STATIC_LIBRARIES})
  endif()

  list(APPEND deps
    ${LCM_LIBRARIES}
  )

  list(APPEND wrap_files
    wrapped_methods_lcm.txt
  )

endif()

message("Use perception is " ${USE_PERCEPTION})
if (USE_PERCEPTION)

  find_package(LibBot REQUIRED MODULE)
  include_directories(${LIBBOT_INCLUDE_DIRS})

  find_package(PCL REQUIRED)
  include_directories(${PCL_INCLUDE_DIRS})

  find_package(OpenCV REQUIRED)
  include_directories(${OpenCV_INCLUDE_DIRS})

  find_package(ZLIB REQUIRED)

  find_package(bot2-core-lcmtypes REQUIRED)

  find_library(CVUTILS_LIBRARY cv-utils DOC "The cv-utils library")
  find_path(CVUTILS_INCLUDE_DIR image_utils/jpeg.h PATH_SUFFIXES cv-utils DOC "Path to the cv-utils include directory")

  set(CVUTILS_INCLUDE_DIRS ${CVUTILS_INCLUDE_DIR})
  set(CVUTILS_LIBRARIES ${CVUTILS_LIBRARY})
  include_directories(${CVUTILS_INCLUDE_DIRS})

  set(moc_srcs)
  qt_wrap_cpp(moc_srcs
    ddBotImageQueue.h
    #ddKinectLCM.h
    ddPointCloudLCM.h
  )

  list(APPEND srcs
    ${moc_srcs}
    ddBotImageQueue.cpp
    #ddKinectLCM.cpp
    ddPointCloudLCM.cpp
  )

  list(APPEND wrap_files
    wrapped_methods_drc.txt
  )

  list (APPEND deps
    ${LIBBOT_LIBRARIES}
    ${PCL_LIBRARIES}
    ${OpenCV_LIBS}
    ${CVUTILS_LIBRARIES}
    ${ZLIB_LIBRARIES}
    ${bot2-core-lcmtypes_LIBRARIES}
  )

endif()

#####



add_library(ddApp SHARED ${srcs})
target_link_libraries(ddApp ${deps})

if(pkg_deps)
  use_pkg(ddApp ${pkg_deps})
endif()

set(_install_targets ddApp directorPython drake-visualizer)

add_executable(drake-visualizer drakeVisualizerApp.cpp)
target_link_libraries(drake-visualizer ddApp)

if(USE_DRC)
  add_executable(director main.cpp)
  target_link_libraries(director ddApp)
  list(APPEND _install_targets director)
endif()

add_executable(directorPython consoleApp.cpp)
target_link_libraries(directorPython ddApp ${PYTHON_LIBRARIES})

install(TARGETS ${_install_targets}
    EXPORT ${DD_TARGETS_NAME}
    RUNTIME DESTINATION ${DD_INSTALL_BIN_DIR}
    LIBRARY DESTINATION ${DD_INSTALL_LIB_DIR}
    ARCHIVE DESTINATION ${DD_INSTALL_LIB_DIR})

if(APPLE)
  install(DIRECTORY drake-visualizer.app
      DESTINATION ${DD_INSTALL_BIN_DIR}
      USE_SOURCE_PERMISSIONS)
endif()

add_custom_command(OUTPUT ${decorator_file}
  COMMAND ${PYTHON_EXECUTABLE} WrapPythonQt.py
  -i ${wrap_files} -o "${decorator_file}"
  --class-prefixes vtk dd QVTK --qt-class-prefixes dd QVTK
  --export-header ddAppConfigure.h --export-symbol DD_APP_EXPORT
  --auto-class-includes --module-name dd
  DEPENDS ${wrap_files} WrapPythonQt.py
  WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR})
