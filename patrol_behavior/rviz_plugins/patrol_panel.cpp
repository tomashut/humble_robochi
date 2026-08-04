/*
 * Plugin de RViz2: PatrolPanel
 * Muestra botones para pausar y reanudar la patrulla
 */

#include <QPushButton>
#include <QVBoxLayout>
#include <QWidget>

#include <rviz_common/panel.hpp>
#include <rclcpp/rclcpp.hpp>
#include <std_srvs/srv/trigger.hpp>

namespace patrol_panel_rviz
{

class PatrolPanel : public rviz_common::Panel
{
Q_OBJECT

public:
  PatrolPanel(QWidget *parent = 0)
  : Panel(parent)
  {
    auto layout = new QVBoxLayout;

    pause_button_ = new QPushButton("Pausar patrulla");
    resume_button_ = new QPushButton("Reanudar patrulla");

    layout->addWidget(pause_button_);
    layout->addWidget(resume_button_);

    setLayout(layout);

    node_ = std::make_shared<rclcpp::Node>("patrol_panel_rviz_node");
    pause_client_ = node_->create_client<std_srvs::srv::Trigger>("/pause_patrol");
    resume_client_ = node_->create_client<std_srvs::srv::Trigger>("/resume_patrol");

    rclcpp::executors::SingleThreadedExecutor::SharedPtr exec = std::make_shared<rclcpp::executors::SingleThreadedExecutor>();
    exec->add_node(node_);
    spinner_ = std::thread([exec]() { exec->spin(); });

    connect(pause_button_, SIGNAL(clicked()), this, SLOT(onPauseClicked()));
    connect(resume_button_, SIGNAL(clicked()), this, SLOT(onResumeClicked()));
  }

  ~PatrolPanel() override {
    spinner_.detach();
  }

public Q_SLOTS:
  void onPauseClicked() {
    if (!pause_client_->wait_for_service(std::chrono::seconds(1))) return;
    auto req = std::make_shared<std_srvs::srv::Trigger::Request>();
    pause_client_->async_send_request(req);
  }

  void onResumeClicked() {
    if (!resume_client_->wait_for_service(std::chrono::seconds(1))) return;
    auto req = std::make_shared<std_srvs::srv::Trigger::Request>();
    resume_client_->async_send_request(req);
  }

protected:
  QPushButton *pause_button_;
  QPushButton *resume_button_;
  rclcpp::Node::SharedPtr node_;
  rclcpp::Client<std_srvs::srv::Trigger>::SharedPtr pause_client_;
  rclcpp::Client<std_srvs::srv::Trigger>::SharedPtr resume_client_;
  std::thread spinner_;
};

} // namespace patrol_panel_rviz

#include <pluginlib/class_list_macros.hpp>
PLUGINLIB_EXPORT_CLASS(patrol_panel_rviz::PatrolPanel, rviz_common::Panel) // <-- Esto hace visible el plugin

